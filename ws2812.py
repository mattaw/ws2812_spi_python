#!/usr/bin/env python
"""Module to drive ws2812 from SPI

Copyright 2021 Matthew A. Swabey

SPDX Apache License 2.0
"""

import logging
import re
import time
from threading import BoundedSemaphore, Event, Thread
from typing import TYPE_CHECKING

import attr
import numpy as np
from spidev import SpiDev

if TYPE_CHECKING:
    from pathlib import Path
    from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


np.set_printoptions(formatter={"int": lambda i: f"{i:3}"})


class SPIws2821BusNotFound(Exception):
    pass


@attr.s
class SPIws2812:
    """Class to drive a string of ws2812 attached to a SPI bus.

    Notes:
        The MOSI line idles high. This causes the first LED to be green most of
        the time as the MSB of the GRB 24 bit code is seen as a "1". Clear it out
        by appending a bus reset of RESET_BYTES_COUNT of 0b0 bytes on the front of any transmission.

        This class uses RESET_BYTES_COUNT of 0b0 bytes + 24 bytes for each led,
        8 green, 8 red and 8 blue.
        Using numpy arrays should be fast as SpiDev.writebytes2 consumes them directly without
        copying.
    """

    spidev: SpiDev = attr.ib()
    num_leds: int = attr.ib()
    led_string_ones: np.ndarray = attr.ib()
    led_string_zeros: np.ndarray = attr.ib()
    tx_buf_clear: np.ndarray = attr.ib()
    tx_buf: np.ndarray = attr.ib()
    tx_thread: "Optional[SimpleTimer]" = attr.ib()
    tx_thread_stop: Event = attr.ib()
    tx_array_lock: BoundedSemaphore = attr.ib()
    fps: int = attr.ib()
    tx_array: "Optional[np.ndarray]" = attr.ib()

    LED_ZERO = 0b1100_0000  # ws2812 "0" 0.15385us * 2 "1's" = 0.308us
    LED_ONE = 0b1111_1100  # ws2812 "1" 0.15385us * 6 "1's" = 0.923us
    RESET_BYTES_COUNT = 42  # 51.7us of flatline output

    class SimpleTimer(Thread):
        """Runs inside and is responsible for animations.

        It accesses its parent to do things, which is rather
        suboptimal but made creating it easier.
        """

        def __init__(self, parent: "SPIws2812", *args, **kwargs):
            Thread.__init__(self, *args, **kwargs)
            self.parent = parent
            self.index = 0

        def run(self):
            while not self.parent.tx_thread_stop.wait(1 / self.parent.fps):
                logger.debug("Writing to LEDs")
                with self.parent.tx_array_lock:
                    rows, _ = self.parent.tx_array.shape
                    if self.index >= rows:
                        self.index = 0
                    self.parent.write_array(self.parent.tx_array[self.index])
                self.index += 1

    @staticmethod
    def bus_cs_from_path(spidev: "Path") -> "Tuple[int, int]":
        """Take in a path to a spidev device node in the form /dev/spidevX.Y
        and returns a tuple of (bus, cs) from it"""
        p = re.compile("/dev/spidev(\d+).(\d+)")
        match = p.match(str(spidev))
        if match:
            bus, cs = match.group(1, 2)
            return (int(bus), int(cs))
        raise SPIws2821BusNotFound(f"Path {str(spidev)} is not ")

    @classmethod
    def init(cls, spi_bus_cs: "Tuple[int,int]", num_leds: int) -> "SPIws2812":
        """Initialize an instance of this class correctly from supplied info.

        Use instead of SPIws2812()

        Args:
            spi_bus_cs: (bus, cs) - from /dev/spidev1.0 the bus is 1, and the cs is 0
                        so (1, 0)
            num_leds: The number of leds in the string of ws2812 leds
        Returns:
            Fully initialized SPIws2812 class, ready to write
        """

        spi = SpiDev()
        spi.open(spi_bus_cs[0], spi_bus_cs[1])
        spi.max_speed_hz = 6_500_000
        spi.mode = 0b00
        spi.lsbfirst = False

        tx_unpacked_ones = np.full(num_leds * 24, SPIws2812.LED_ONE, dtype=np.uint8)
        tx_unpacked_zeros = np.full(num_leds * 24, SPIws2812.LED_ZERO, dtype=np.uint8)
        tx_buf_clear = np.zeros(cls.RESET_BYTES_COUNT + num_leds * 24, dtype=np.uint8)
        tx_buf_clear[cls.RESET_BYTES_COUNT :] = np.full(
            num_leds * 24, SPIws2812.LED_ZERO, dtype=np.uint8
        )
        tx_buf = np.zeros(cls.RESET_BYTES_COUNT + num_leds * 24, dtype=np.uint8)

        tx_array_lock = BoundedSemaphore(1)
        tx_thread_stop = Event()
        instance = cls(
            spidev=spi,
            num_leds=num_leds,
            led_string_ones=tx_unpacked_ones,
            led_string_zeros=tx_unpacked_zeros,
            tx_buf_clear=tx_buf_clear,
            tx_buf=tx_buf,
            tx_thread=None,
            tx_thread_stop=tx_thread_stop,
            tx_array_lock=tx_array_lock,
            tx_array=None,
            fps=60,
        )
        return instance

    def clear(self) -> None:
        """Reset all LEDs to off, stop worker"""
        self.stop()
        self.spidev.writebytes2(self.tx_buf_clear)

    def write(self, data: "List[List[int]]") -> None:
        """Set the colors of the LED string.

        Each LED is set by a list of 3 integers between 0-255 where 0 is off and
        255 is maximum brightness. The order of color is [G, R, B]. For a string
        of 2 LEDs, the one closest to the SPI bus is 0, the next 1, etc. the full
        specification for the string is [[G0, R0, B0], [G1, R1, B1]]

        If the list is too short it gets padded with [0,0,0], if too long trimmed

        Args:
            data: List of [[G, R, B],...] values for each ws2812 LED
        """
        length_diff = self.num_leds - len(data)
        if length_diff > 0:
            logger.debug("data too short, padding by %s", length_diff)
            data = data + [[0, 0, 0]] * length_diff
        elif length_diff < 0:
            logger.debug("data too long, trimming by %s", -length_diff)
            del data[length_diff:]
        tx_data = np.array(data, dtype=np.uint8).ravel()
        logger.debug("%s", tx_data)
        tx_data_unpacked = np.unpackbits(tx_data)
        self.tx_buf[self.RESET_BYTES_COUNT :] = np.where(
            tx_data_unpacked == 1, self.led_string_ones, self.led_string_zeros
        )
        self.spidev.writebytes2(self.tx_buf)

    def write_array(self, data: np.ndarray) -> None:
        """Set the colors of the led string by a 1D np.Array of uint8s

        Each LED is set in G R B order by the input array. Note, no checking
        is done so it must by num_leds * 3 long in GRB order already.

        Args:
            np.Array in uint8 form of num_leds * 3 length in GRB order
        """
        tx_data_unpacked = np.unpackbits(data)
        self.tx_buf[self.RESET_BYTES_COUNT :] = np.where(
            tx_data_unpacked == 1, self.led_string_ones, self.led_string_zeros
        )
        self.spidev.writebytes2(self.tx_buf)

    def start(self) -> None:
        """Start the worker thread to animate LEDs."""
        if self.tx_thread is None or not self.tx_thread.is_alive():
            logger.info("Starting worker")
            self.tx_thread = self.SimpleTimer(self, name="LED-Worker")
            self.tx_thread_stop.clear()
            self.tx_thread.start()
        else:
            logger.debug("Worker already running")

    def stop(self) -> None:
        """Halt the worker thread if its running."""
        logger.info("Stopping worker if running")
        if self.tx_thread is not None and self.tx_thread.is_alive():
            self.tx_thread_stop.set()
            self.tx_thread.join()
            logger.debug("Running worker stopped")
            return

    def breathe(self, color: "List[int]") -> None:
        """Drive the leds with a breathing pattern based on one color.

        Args:
            color: List of 3 ints in GRB
        """
        cos_lookup = (
            np.cos(np.linspace(np.pi, np.pi * 3, self.fps)) + 1
        ) * 0.5  # Starts at intensity zero -> 1
        color_lookup = np.tile(
            np.array(color, dtype=np.uint8), (self.fps, self.num_leds)
        )
        cos_color_lookup = np.multiply(
            color_lookup,
            cos_lookup[:, np.newaxis],
        ).astype(np.uint8)
        with self.tx_array_lock:
            self.tx_array = cos_color_lookup
        self.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import argparse

    parser = argparse.ArgumentParser()
    parser.parse_args()

    spi = SPIws2812.init((1, 0), 4)

    lookup_max = 50
    sin_lookup = (np.cos(np.linspace(np.pi, np.pi * 3, lookup_max)) + 1) * 0.5
    led_colors = np.array([[255, 0, 0]] * 4)
    index = 0
    while True:
        led_colors_now = led_colors * sin_lookup[index]
        spi.write(led_colors_now)
        index = index + 1 if index < lookup_max - 1 else 0
        time.sleep(0.02)
