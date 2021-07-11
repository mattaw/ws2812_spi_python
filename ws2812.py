#!/usr/bin/env python
"""Module to drive ws2812 from SPI

Copyright 2021 Matthew A. Swabey

SPDX Apache License 2.0
"""

import logging
import time
from typing import TYPE_CHECKING

import attr
import numpy as np
from spidev import SpiDev

if TYPE_CHECKING:
    from typing import List, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


np.set_printoptions(formatter={"int": lambda i: f"{i:3}"})


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
    led_string_ones = attr.ib()
    led_string_zeros = attr.ib()
    tx_buf_clear = attr.ib()
    tx_buf = attr.ib()

    LED_ZERO = 0b1100_0000  # ws2812 "0" 0.15385us * 2 "1's" = 0.308us
    LED_ONE = 0b1111_1100  # ws2812 "1" 0.15385us * 6 "1's" = 0.923us
    RESET_BYTES_COUNT = 42  # 51.7us of flatline output

    @classmethod
    def init(cls, spi_bus_cs: "Tuple[int,int]", num_leds: int) -> "SPIws2812":
        """Initialize an instance of this class correctly from supplied info.

        Use instead of SPIws2812()

        Args:
            spi_bus_cs: Two integers representing the bus and the chip select.
                        From /dev/spidev1.0 the bus is 1, and the cs is 0
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

        return cls(
            spidev=spi,
            num_leds=num_leds,
            led_string_ones=tx_unpacked_ones,
            led_string_zeros=tx_unpacked_zeros,
            tx_buf_clear=tx_buf_clear,
            tx_buf=tx_buf,
        )

    def clear(self) -> None:
        """Reset all LEDs to off."""
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.parse_args()

    spi = SPIws2812.init((1, 0), 4)

    lookup_max = 50
    sin_lookup = (np.sin(np.linspace(0, np.pi * 2, lookup_max)) + 1) * 0.5
    led_colors = np.array([[255, 0, 0]] * 4)
    index = 0
    while True:
        led_colors_now = led_colors * sin_lookup[index]
        spi.write(led_colors_now)
        index = index + 1 if index < lookup_max - 1 else 0
        time.sleep(0.02)
