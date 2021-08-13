# ws2812-spi-python
A very simple class to control a string of ws2812 LED strips using SPI interfaces on Raspberry Pi/Orange Pi/etc. single board computers (SBCs) running Linux. It requires:
1. The `numpy` package to work around the limited capabilities of a low-CPU-power computer. Python is quite expensive to do maths in, as well as move data around but this package, along with the Python package `spidev` and `numpy` use the buffer protocol to massively improve this (https://docs.python.org/3/c-api/buffer.html).
   1. Try to precompute the colors you need in memory in an `numpy.array(... , dtype=uint8)` and **then** use a loop to write them with a slice `foo[5:50]` as this prevents Python allocating memory for, and converting every single byte into a `uint8`, every time it updates the LED string.
   1. If not using `numpy` arrays use simple Python `list` instead as this will be 1000x faster than iterating through a `dict` or `set`. (Leverage Python buffer protocol using `numpy.array` or `list` to transfer content by reference to `spidev.writebytes2()`.
3. An activated SPI port on your particular SBC using device overlays so the file `/dev/spidevX.Y` (typically `/dev/spidev0.0`) appears on your SBC.
   1. SPI on a Rasberry Pi: https://www.raspberrypi.org/documentation/hardware/raspberrypi/spi/README.md.
   2. An example on Armbian (typically /boot/armbianEnv.txt) the OrangePi: https://unix.stackexchange.com/questions/496070/how-to-enable-spi-on-orange-pi-pc-in-armbian.
   4. User access to the SPI device node in `/dev`. 
      1. An example `udev` rule is provided, `99-spidev.rules`, which can be copied into `/etc/udev/rules.d`. (It will not take effect until `udev` is reloaded or your SBC rebooted.)
      1. The example `udev` rule makes the `/dev/spidevX.Y` nodes available to members of the group `spidev` - this may not be present on your system so you can create it with `sudo addgroup spidev`. Remember to add the user to this new group with `sudo adduser username spidev`.

Please note that an OrangePi Zero (32 bit ARM w. 512MB of RAM) can drive a string of >100 consistently with 1ms updates @5% CPU using a precomputed lookup table to generate the bytes sent out of the SPI bus. A normal SBC **cannot** power many LEDs - remember to use a separate high current DC supply if using 10+!). Start with just 3-4 if you are powering it from the SBC to test it out, when it is working you can increase the numbers to find out the limits of your particular SPI driver, CPU, RAM combination. If you do not use a separate supply you could possibly damage your SBC.

## Development Requirements

On an Ubuntu or Debian system:

```
sudo dash -c "apt update; apt dist-upgrade -y; apt install -y python3-pip python3-setuptools python3-dev python3-numpy"
git clone https://github.com/mattaw/ws2812-spi-python.git
cd ws2812-spi-python
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -U pip setuptools pip-tools
pip-sync requirements.txt

```

If you wish to modify the code, or develop it further install the development packages with:
```
pip-sync dev-requirements.txt
```

