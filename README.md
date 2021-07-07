# ws2812-spi-python
A very simple class to control a string of ws2812 LED strips using SPI interfaces on Raspberry Pi/Orange Pi/etc. single board computers. It requires numpy.

Please note that an OrangePi Zero w. 512MB of RAM can drive a string of >100 (however it cannot power the string - remember to use a high current DC supply!).

## Requirements

On an Ubuntu or Debian system:

```
sudo dash -c "apt update; apt dist-upgrade -y; apt install -y python3-pip python3-setuptools python3-dev python3-numpy"
git clone https://github.com/mattaw/ws2812-spi-python.git
cd ws2812-spi-python
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools pip-tools
pip-sync requirements.txt

```

On development systems:
```
pip-sync dev-requirements.txt
```
