#!/bin/bash
# NanoPi NEO OLED Monitor - Silent One-Step Installation
# This script runs completely silently without any user prompts

set -e

# Redirect all output to log file and show progress
INSTALL_LOG="/tmp/nanopi_oled_install.log"
exec 1> >(tee -a "$INSTALL_LOG")
exec 2>&1

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')] $1${NC}"
}

print_success() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')] ✓ $1${NC}"
}

print_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')] ✗ $1${NC}"
    exit 1
}

# Silent execution wrapper
silent_run() {
    "$@" >/dev/null 2>&1
}

echo "=== NanoPi NEO OLED Monitor - Silent Installation ==="
echo "Installation log: $INSTALL_LOG"
echo ""

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   print_error "Please run as regular user (not root). Script will use sudo when needed."
fi

# Check system compatibility
if ! command -v apt-get &> /dev/null; then
    print_error "Requires Debian/Ubuntu-based system"
fi

# Auto-detect user
USER_NAME=$(whoami)
USER_HOME=$(eval echo ~$USER_NAME)

print_status "Installing for user: $USER_NAME"
print_status "Home directory: $USER_HOME"

# Update system packages silently
print_status "Updating system packages..."
silent_run sudo apt-get update -qq
print_success "System updated"

# Install system dependencies silently
print_status "Installing system dependencies..."
silent_run sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    python3-setuptools \
    python3-venv \
    git \
    i2c-tools \
    ntpdate \
    ntp \
    build-essential \
    libjpeg-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7 \
    libtiff5 \
    tk-dev \
    tcl-dev \
    zlib1g-dev \
    libwebp-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev
print_success "Dependencies installed"

# Enable I2C interface silently
print_status "Configuring I2C interface..."

# Add i2c modules
if ! grep -q "^i2c-dev" /etc/modules 2>/dev/null; then
    echo "i2c-dev" | sudo tee -a /etc/modules >/dev/null
fi

# Configure boot settings
BOOT_CONFIG=""
for config_path in "/boot/config.txt" "/boot/firmware/config.txt" "/boot/firmware/usercfg.txt"; do
    if [[ -f "$config_path" ]]; then
        BOOT_CONFIG="$config_path"
        break
    fi
done

if [[ -n "$BOOT_CONFIG" ]]; then
    if ! grep -q "^dtparam=i2c_arm=on" "$BOOT_CONFIG" 2>/dev/null; then
        echo "dtparam=i2c_arm=on" | sudo tee -a "$BOOT_CONFIG" >/dev/null
    fi
    if ! grep -q "^dtparam=i2c1_baudrate" "$BOOT_CONFIG" 2>/dev/null; then
        echo "dtparam=i2c1_baudrate=100000" | sudo tee -a "$BOOT_CONFIG" >/dev/null
    fi
fi

# Add user to i2c and gpio groups
silent_run sudo usermod -a -G i2c,gpio "$USER_NAME" 2>/dev/null || true

print_success "I2C configured"

# Create application directory
APP_DIR="$USER_HOME/nanopi-oled-monitor"
print_status "Creating application directory..."
mkdir -p "$APP_DIR"
cd "$APP_DIR"
print_success "Directory created: $APP_DIR"

# Create Python virtual environment
print_status "Setting up Python environment..."
silent_run python3 -m venv venv
source venv/bin/activate
silent_run pip install --upgrade pip setuptools wheel
print_success "Virtual environment ready"

# Install Python dependencies
print_status "Installing Python packages..."
silent_run pip install \
    luma.oled \
    psutil \
    pytz \
    RPi.GPIO \
    Pillow \
    adafruit-circuitpython-ssd1306 \
    requests
print_success "Python packages installed"

# Create main application file
print_status "Creating application..."
cat > "$APP_DIR/nanopi_oled_monitor.py" << 'EOF'
#!/usr/bin/env python3
"""
NanoPi NEO OLED System Monitor - Silent Auto-Install Version
"""

import os
import sys
import time
import json
import threading
import subprocess
from datetime import datetime
import signal
import logging
from pathlib import Path
import socket

# Auto-install packages silently
def install_packages():
    required_packages = ['luma.oled', 'psutil', 'pytz', 'RPi.GPIO', 'Pillow']
    for package in required_packages:
        try:
            __import__(package.replace('-', '_').replace('.', '_'))
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--user', package], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    install_packages()
except:
    pass

# Import packages
try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw, ImageFont
    import RPi.GPIO as GPIO
    import psutil
    import pytz
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

class NanoPiOLEDMonitor:
    def __init__(self):
        self.config_file = Path.home() / '.nanopi_monitor_config.json'
        self.log_file = Path.home() / '.nanopi_monitor.log'
        
        # Setup minimal logging
        logging.basicConfig(
            level=logging.WARNING,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.FileHandler(self.log_file)]
        )
        self.logger = logging.getLogger(__name__)
        
        self.config = self.load_config()
        self.display_modes = ['datetime', 'system_info', 'network_info', 'temperature', 'uptime']
        self.current_mode = 0
        self.button_pins = [16, 20, 21]
        
        self.setup_gpio()
        self.setup_display()
        
        self.timezone = pytz.timezone(self.config.get('timezone', 'UTC'))
        self.running = True
        self.display_lock = threading.Lock()
        self.last_ntp_sync = 0
        self.ntp_sync_interval = 3600

    def load_config(self):
        default_config = {
            'timezone': 'UTC',
            'display_brightness': 255,
            'ntp_servers': ['pool.ntp.org', 'time.google.com'],
            'refresh_rate': 1.0,
            'temperature_unit': 'C',
            'show_seconds': True
        }
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    default_config.update(config)
            except:
                pass
        return default_config

    def save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except:
            pass

    def setup_gpio(self):
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in self.button_pins:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                GPIO.add_event_detect(pin, GPIO.FALLING, callback=self.button_callback, bouncetime=300)
        except:
            pass

    def setup_display(self):
        try:
            addresses = [0x3C, 0x3D]
            ports = [1, 0]
            self.device = None
            
            for port in ports:
                for addr in addresses:
                    try:
                        serial = i2c(port=port, address=addr)
                        self.device = ssd1306(serial, width=128, height=64)
                        self.device.contrast(self.config['display_brightness'])
                        return
                    except:
                        continue
        except:
            self.device = None

    def button_callback(self, channel):
        try:
            if channel == self.button_pins[0]:
                self.current_mode = (self.current_mode + 1) % len(self.display_modes)
            elif channel == self.button_pins[1]:
                self.cycle_timezone()
            elif channel == self.button_pins[2]:
                threading.Thread(target=self.sync_ntp, daemon=True).start()
        except:
            pass

    def cycle_timezone(self):
        timezones = ['UTC', 'US/Eastern', 'US/Pacific', 'Europe/London', 'Europe/Berlin',
                    'Asia/Shanghai', 'Asia/Tokyo', 'Australia/Sydney', 'America/New_York',
                    'America/Los_Angeles', 'Asia/Kolkata']
        try:
            current_tz = self.config['timezone']
            current_index = timezones.index(current_tz) if current_tz in timezones else 0
            next_index = (current_index + 1) % len(timezones)
            self.config['timezone'] = timezones[next_index]
            self.timezone = pytz.timezone(timezones[next_index])
            self.save_config()
        except:
            pass

    def sync_ntp(self):
        try:
            for server in self.config['ntp_servers']:
                try:
                    subprocess.check_call(['sudo', 'ntpdate', '-s', server], 
                                        timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.last_ntp_sync = time.time()
                    return True
                except:
                    continue
            return False
        except:
            return False

    def get_system_info(self):
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            return {
                'cpu': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used': memory.used // (1024**2),
                'memory_total': memory.total // (1024**2),
                'disk_percent': disk.percent,
                'disk_used': disk.used // (1024**3),
                'disk_total': disk.total // (1024**3)
            }
        except:
            return None

    def get_network_info(self):
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
            ip_addresses = result.stdout.strip().split()
            net_io = psutil.net_io_counters()
            return {
                'ip_addresses': ip_addresses,
                'bytes_sent': net_io.bytes_sent // (1024**2),
                'bytes_recv': net_io.bytes_recv // (1024**2),
            }
        except:
            return None

    def get_temperature(self):
        try:
            temp_files = ['/sys/class/thermal/thermal_zone0/temp', '/sys/class/hwmon/hwmon0/temp1_input']
            for temp_file in temp_files:
                try:
                    with open(temp_file, 'r') as f:
                        return int(f.read().strip()) / 1000.0
                except:
                    continue
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            return entries[0].current
            except:
                pass
            return None
        except:
            return None

    def get_uptime(self):
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            return {'days': days, 'hours': hours, 'minutes': minutes}
        except:
            return None

    def draw_datetime(self, draw, width, height):
        try:
            now = datetime.now(self.timezone)
            date_str = now.strftime("%a, %b %d %Y")
            time_str = now.strftime("%H:%M:%S" if self.config['show_seconds'] else "%H:%M")
            tz_str = str(self.timezone).split('/')[-1]
            
            draw.text((0, 0), date_str, fill="white")
            draw.text((0, 16), time_str, fill="white")
            draw.text((0, 32), f"TZ: {tz_str}", fill="white")
            
            time_since_sync = time.time() - self.last_ntp_sync
            ntp_status = "Synced" if time_since_sync < 7200 else "Old"
            draw.text((0, 48), f"NTP: {ntp_status}", fill="white")
        except Exception as e:
            draw.text((0, 0), f"Time Error", fill="white")

    def draw_system_info(self, draw, width, height):
        try:
            info = self.get_system_info()
            if not info:
                draw.text((0, 0), "System info unavailable", fill="white")
                return
            
            draw.text((0, 0), f"CPU: {info['cpu']:.1f}%", fill="white")
            draw.text((0, 12), f"RAM: {info['memory_percent']:.1f}%", fill="white")
            draw.text((0, 24), f"     {info['memory_used']}/{info['memory_total']}MB", fill="white")
            draw.text((0, 36), f"Disk: {info['disk_percent']:.1f}%", fill="white")
            draw.text((0, 48), f"      {info['disk_used']}/{info['disk_total']}GB", fill="white")
        except:
            draw.text((0, 0), "System Error", fill="white")

    def draw_network_info(self, draw, width, height):
        try:
            info = self.get_network_info()
            if not info:
                draw.text((0, 0), "Network unavailable", fill="white")
                return
            
            draw.text((0, 0), "Network Info", fill="white")
            y_pos = 12
            if info['ip_addresses']:
                for ip in info['ip_addresses'][:2]:
                    draw.text((0, y_pos), f"IP: {ip}", fill="white")
                    y_pos += 12
            else:
                draw.text((0, y_pos), "No IP address", fill="white")
                y_pos += 12
            
            draw.text((0, y_pos), f"TX: {info['bytes_sent']}MB", fill="white")
            draw.text((0, y_pos + 12), f"RX: {info['bytes_recv']}MB", fill="white")
        except:
            draw.text((0, 0), "Network Error", fill="white")

    def draw_temperature(self, draw, width, height):
        try:
            temp = self.get_temperature()
            draw.text((0, 0), "Temperature", fill="white")
            
            if temp is not None:
                if self.config['temperature_unit'] == 'F':
                    temp_f = (temp * 9/5) + 32
                    draw.text((0, 16), f"CPU: {temp_f:.1f}°F", fill="white")
                    draw.text((0, 28), f"     {temp:.1f}°C", fill="white")
                else:
                    draw.text((0, 16), f"CPU: {temp:.1f}°C", fill="white")
                
                status = "COOL" if temp < 50 else "WARM" if temp < 70 else "HOT!"
                draw.text((0, 40), f"Status: {status}", fill="white")
            else:
                draw.text((0, 16), "Sensor unavailable", fill="white")
        except:
            draw.text((0, 0), "Temp Error", fill="white")

    def draw_uptime(self, draw, width, height):
        try:
            uptime = self.get_uptime()
            draw.text((0, 0), "System Uptime", fill="white")
            
            if uptime:
                draw.text((0, 16), f"Days: {uptime['days']}", fill="white")
                draw.text((0, 28), f"Hours: {uptime['hours']}", fill="white")
                draw.text((0, 40), f"Minutes: {uptime['minutes']}", fill="white")
            else:
                draw.text((0, 16), "Uptime unavailable", fill="white")
        except:
            draw.text((0, 0), "Uptime Error", fill="white")

    def update_display(self):
        try:
            with self.display_lock:
                if not self.device:
                    return
                
                with canvas(self.device) as draw:
                    mode = self.display_modes[self.current_mode]
                    
                    if mode == 'datetime':
                        self.draw_datetime(draw, 128, 64)
                    elif mode == 'system_info':
                        self.draw_system_info(draw, 128, 64)
                    elif mode == 'network_info':
                        self.draw_network_info(draw, 128, 64)
                    elif mode == 'temperature':
                        self.draw_temperature(draw, 128, 64)
                    elif mode == 'uptime':
                        self.draw_uptime(draw, 128, 64)
        except:
            pass

    def auto_ntp_sync(self):
        if time.time() - self.last_ntp_sync > self.ntp_sync_interval:
            threading.Thread(target=self.sync_ntp, daemon=True).start()

    def display_thread(self):
        while self.running:
            try:
                self.auto_ntp_sync()
                self.update_display()
                time.sleep(self.config['refresh_rate'])
            except:
                time.sleep(5)

    def signal_handler(self, signum, frame):
        self.running = False

    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        threading.Thread(target=self.sync_ntp, daemon=True).start()
        
        display_thread = threading.Thread(target=self.display_thread)
        display_thread.daemon = True
        display_thread.start()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()

    def cleanup(self):
        self.running = False
        try:
            if self.device:
                self.device.cleanup()
        except:
            pass
        try:
            GPIO.cleanup()
        except:
            pass

if __name__ == "__main__":
    monitor = NanoPiOLEDMonitor()
    monitor.run()
EOF

chmod +x "$APP_DIR/nanopi_oled_monitor.py"
print_success "Application created"

# Create wrapper script
print_status "Creating launcher script..."
cat > "$APP_DIR/run_monitor.sh" << EOF
#!/bin/bash
cd "$APP_DIR"
source venv/bin/activate
python nanopi_oled_monitor.py
EOF

chmod +x "$APP_DIR/run_monitor.sh"
print_success "Launcher created"

# Create systemd service
print_status "Setting up auto-start service..."
sudo tee /etc/systemd/system/nanopi-oled-monitor.service >/dev/null << EOF
[Unit]
Description=NanoPi OLED Monitor
After=network.target
Wants=network.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/run_monitor.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

silent_run sudo systemctl daemon-reload
silent_run sudo systemctl enable nanopi-oled-monitor.service
print_success "Auto-start configured"

# Create uninstall script
cat > "$APP_DIR/uninstall.sh" << EOF
#!/bin/bash
sudo systemctl stop nanopi-oled-monitor.service 2>/dev/null || true
sudo systemctl disable nanopi-oled-monitor.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/nanopi-oled-monitor.service
sudo systemctl daemon-reload
rm -rf "$APP_DIR"
echo "NanoPi OLED Monitor uninstalled."
EOF

chmod +x "$APP_DIR/uninstall.sh"

# Test I2C (silently)
HAS_OLED=false
if command -v i2cdetect >/dev/null 2>&1; then
    if i2cdetect -y 1 2>/dev/null | grep -q "3c\|3d"; then
        HAS_OLED=true
    fi
fi

# Start service automatically
print_status "Starting service..."
if sudo systemctl start nanopi-oled-monitor.service 2>/dev/null; then
    print_success "Service started successfully"
else
    print_status "Service will start after reboot"
fi

# Final status
echo ""
echo "=== Installation Complete ==="
print_success "NanoPi OLED Monitor installed successfully!"
echo ""
echo "Installation Details:"
echo "• Location: $APP_DIR"
echo "• Service: nanopi-oled-monitor"
echo "• Auto-start: Enabled"
echo "• Log file: $INSTALL_LOG"
echo ""

if [[ "$HAS_OLED" == "true" ]]; then
    print_success "OLED display detected and ready"
else
    echo "• OLED display: Connect to I2C (SDA=GPIO2, SCL=GPIO3)"
fi

echo ""
echo "Quick Commands:"
echo "• Check status: sudo systemctl status nanopi-oled-monitor"
echo "• View logs: journalctl -u nanopi-oled-monitor -f"
echo "• Uninstall: $APP_DIR/uninstall.sh"
echo ""

# Check if reboot needed
if ! lsmod | grep -q i2c_dev 2>/dev/null; then
    echo "⚠️  Reboot recommended to enable I2C interface"
    echo "   After reboot, the monitor will start automatically"
else
    print_success "Ready to use! Check your OLED display."
fi

echo ""
echo "Installation completed at $(date)"
echo "==================================="