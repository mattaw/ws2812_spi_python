# ws2812-spi-python
A very simple class to control a string of ws2812 LED strips using SPI interfaces on Raspberry Pi/Orange Pi/etc. single board computers (SBCs) running Linux. It requires:
   1. The numpy package to work around the limited capabilities of a low-CPU-power computer as Python is quite expensive to do maths in, as well as move data around.
   1. An activated SPI port on your particular SBC using device overlays so the file /dev/spidevX.Y (typically /dev/spidev0.0) appears on your computer.
      1. SPI on a Rasberry Pi: https://www.raspberrypi.org/documentation/hardware/raspberrypi/spi/README.md
      2. An example on Armbian (typically /boot/armbianEnv.txt) the OrangePi: https://unix.stackexchange.com/questions/496070/how-to-enable-spi-on-orange-pi-pc-in-armbian

Please note that an OrangePi Zero (32 bit ARM) w. 512MB of RAM can drive a string of >100 at 1ms updates (it **cannot** power that many LEDs - remember to use a high current DC supply!). Start with just 3-4 to test it out, when it is working you can increase the numbers to find out the limits of your particular SPI driver, CPU, RAM combination.

## Requirements

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

On development systems:
```
pip-sync dev-requirements.txt
```

