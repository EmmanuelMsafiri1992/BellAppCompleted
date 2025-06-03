#!/bin/bash

# Update system and install dependencies
sudo apt-get update -y
sudo apt-get install -y git python3 python3-pip i2c-tools
sudo pip3 install luma.oled

# Enable I2C for OLED communication
sudo raspi-config nonint do_i2c 0

# Set timezone to user's local timezone (or default to UTC if detection fails)
if [ -f /etc/timezone ]; then
    TZ=$(cat /etc/timezone)
else
    TZ="UTC"
fi
sudo timedatectl set-timezone "$TZ"
sudo timedatectl set-ntp true

# Clone FriendlyELEC's NanoHAT OLED repository
git clone https://github.com/friendlyarm/NanoHatOLED.git
cd NanoHATOLED

# Install NanoHAT OLED dependencies
sudo bash ./install.sh

# Create a Python script to display the time on the OLED
cat << EOF > display_time.py
#!/usr/bin/env python3
import time
import datetime
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont

# Initialize the OLED display
serial = i2c(port=1, address=0x3C)
device = ssd1306(serial)

# Load a font (default or custom if available)
font = ImageFont.load_default()

while True:
    with canvas(device) as draw:
        # Get current time
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Display time on OLED
        draw.text((0, 0), current_time, font=font, fill="white")
    time.sleep(1)
EOF

# Make the Python script executable
chmod +x display_time.py

# Create a systemd service to run the script continuously
sudo bash -c 'cat << EOF > /etc/systemd/system/oled-time.service
[Unit]
Description=OLED Time Display Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 /root/NanoHATOLED/display_time.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF'

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable oled-time.service
sudo systemctl start oled-time.service

# Notify user of completion
echo "Setup complete! The OLED display should now show the current time."