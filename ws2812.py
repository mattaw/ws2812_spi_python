#!/usr/bin/env python
"""Module to drive ws2812 from SPI

Copyright 2021 Matthew A. Swabey

SPDX Apache License 2.0
"""

import logging
import re
import time
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from threading import BoundedSemaphore, Event, Thread
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

import attr
import numpy as np  # type: ignore
import pendulum
from cattr import Converter
from pendulum import DateTime
from spidev import SpiDev  # type: ignore

logger = logging.getLogger(__name__)


def to_iso8601(datetime: DateTime) -> str:
    return datetime.to_iso8601_string()


IPType = Union[IPv4Address, IPv6Address]

converter = Converter()
converter.register_unstructure_hook(Path, str)
converter.register_structure_hook(Path, lambda pathstr, _: Path(pathstr))
converter.register_unstructure_hook(DateTime, to_iso8601)
converter.register_structure_hook(DateTime, lambda isostr, _: pendulum.parse(isostr))
converter.register_unstructure_hook(IPv4Address, str)
converter.register_structure_hook(IPv4Address, lambda ipstr, _: ip_address(ipstr))
converter.register_unstructure_hook(IPv6Address, str)
converter.register_structure_hook(IPv6Address, lambda ipstr, _: ip_address(ipstr))
converter.register_structure_hook(IPType, lambda ipstr, _: ip_address(ipstr))


np.set_printoptions(formatter={"int": lambda i: f"{i:3}"})


class SPIws2821BusNotFound(Exception):
    pass


@attr.s
class SPIws2812Config:
    spidev: Path = attr.ib()

    @spidev.validator
    def _check_spidev(self, attribute, value: Path):
        if not value.is_char_device():
            raise ValueError(f"Path '{value}' is not a character device")
        try:
            f = value.open(mode="w")
            f.close()
        except OSError:
            raise ValueError(f"Char device '{value}' cannot be opened read+write")

    num_leds: int = attr.ib()

    @num_leds.validator
    def _check_num_leds(self, attribute, value: int):
        if value <= 0:
            raise ValueError("num_leds must be an integer greater than one")

    bus: int = attr.ib()

    @bus.default
    def _get_bus(self):
        m = self.bus_cs_pattern.match(str(self.spidev))
        if m:
            return int(m.group(1))
        else:
            raise ValueError(
                f"Failed to extract bus (first digit) from spidev '{self.spidev}'"
            )

    cs: int = attr.ib()

    @cs.default
    def _get_cs(self):
        m = self.bus_cs_pattern.match(str(self.spidev))
        if m:
            return int(m.group(2))
        else:
            raise ValueError(
                f"Failed to extract cs (second digit) from spidev '{self.spidev}'"
            )

    bus_cs_pattern: ClassVar[re.Pattern] = re.compile("/dev/spidev(\d+).(\d+)")


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
                with self.parent.tx_array_lock:
                    rows, _ = self.parent.tx_array.shape
                    if self.index >= rows:
                        self.index = 0
                    self.parent.write_array(self.parent.tx_array[self.index])
                self.index += 1

    @classmethod
    def init_from_dict(cls, config_dict: "Dict[str, Any]") -> "SPIws2812":
        config = converter.structure(config_dict, SPIws2812Config)
        return cls.init((config.bus, config.cs), config.num_leds)

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
            fps=30,
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
        if self.tx_thread_stop.is_set():
            logger.info("Worker: not starting as stop set")
            return
        if self.tx_thread is None or not self.tx_thread.is_alive():
            logger.info("Worker: starting")
            self.tx_thread = self.SimpleTimer(self, name="LED-Worker")
            self.tx_thread_stop.clear()
            self.tx_thread.start()
        else:
            logger.debug("Worker: already running")

    def stop(self) -> None:
        """Halt the worker thread if its running."""
        logger.info("Worker: stopping if running")
        if self.tx_thread is not None and self.tx_thread.is_alive():
            self.tx_thread_stop.set()
            self.tx_thread.join()
            logger.debug("Worker: stopped")
            return

    @staticmethod
    def _parse_color(color: "Tuple[int, int, int]") -> "Tuple[int, int, int]":
        grb = [color[1], color[0], color[2]]  # reorder for WS2812 GRB
        grb = [0 if c < 0 else c for c in grb]  # limit min to 0
        grb = [255 if c > 255 else c for c in grb]  # limit max max 25
        return (grb[0], grb[1], grb[2])

    def breathe(self, color: "Tuple[int, int, int]", hz: float = 1) -> None:
        """Drive the leds with a breathing pattern based on one color.

        Args:
            color: Tuple of 3 ints for R,G,B in the range 0-255.
            hz: cycles / second of the pattern
        """
        frames = int(self.fps / hz)
        if frames < 6:
            logger.warn("Cycle time (hz) to fast, clipping")
            frames = 6
        grb = self._parse_color(color)
        cos_lookup = (
            np.cos(np.linspace(np.pi, np.pi * 3, frames)) + 1
        ) * 0.5  # Starts at intensity zero -> 1
        color_lookup = np.tile(np.array(grb, dtype=np.uint8), (frames, self.num_leds))
        cos_color_lookup = np.multiply(
            color_lookup,
            cos_lookup[:, np.newaxis],
        ).astype(np.uint8)
        with self.tx_array_lock:
            self.tx_array = np.copy(cos_color_lookup)
        self.start()

    def chase(
        self, color: "Tuple[int, int, int]", hz: float = 1, clockwise=True
    ) -> None:
        """Chase the led color around the ring in hz complete circuits / s

        Args:
            color: Tuple of 3 ints for R,G,B in the range 0-255.
            hz: cycles / second of the pattern
        """
        frames = int(self.fps / hz)
        rem = frames % self.num_leds
        frames = frames + (self.num_leds - rem)
        frames_per_led = int(frames / self.num_leds)
        logger.debug("Calc tot frames: '%s', frames/led: '%s'", frames, frames_per_led)
        grb = self._parse_color(color)
        lookup = np.zeros((frames, self.num_leds * 3), dtype=np.uint8)
        led = np.array(grb, dtype=np.uint8)
        for f in range(frames):
            start = (f // frames_per_led) * 3
            finish = (f // frames_per_led + 1) * 3
            np.copyto(lookup[f, start:finish], led)
        with self.tx_array_lock:
            if clockwise:
                self.tx_array = np.copy(np.flipud(lookup))
            else:
                self.tx_array = np.copy(lookup)
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
