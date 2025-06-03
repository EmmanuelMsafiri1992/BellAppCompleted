#!/usr/bin/env python3
"""
NanoPi NEO OLED System Monitor
A robust, self-installing system monitor for NanoPi NEO with OLED display
Supports multiple time zones, system monitoring, and button interactions
"""

import os
import sys
import time
import json
import threading
import subprocess
from datetime import datetime, timezone
import pytz
import signal
import logging
from pathlib import Path

# Auto-install required packages
def install_packages():
    """Install required packages if not available"""
    required_packages = [
        'luma.oled',
        'psutil',
        'pytz',
        'RPi.GPIO',
        'Pillow'
    ]
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_').replace('.', '_'))
        except ImportError:
            print(f"Installing {package}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', package])

# Install packages first
install_packages()

# Now import the packages
try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw, ImageFont
    import RPi.GPIO as GPIO
    import psutil
except ImportError as e:
    print(f"Failed to import required modules: {e}")
    print("Please run: pip3 install luma.oled psutil pytz RPi.GPIO Pillow")
    sys.exit(1)

class NanoPiOLEDMonitor:
    def __init__(self):
        self.config_file = Path.home() / '.nanopi_monitor_config.json'
        self.log_file = Path.home() / '.nanopi_monitor.log'
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Configuration
        self.config = self.load_config()
        
        # Display modes
        self.display_modes = [
            'datetime',
            'system_info',
            'network_info',
            'temperature'
        ]
        self.current_mode = 0
        
        # GPIO setup for buttons (F1, F2, F3)
        self.button_pins = [16, 20, 21]  # Adjust based on your wiring
        self.setup_gpio()
        
        # OLED setup
        self.setup_display()
        
        # Time zone
        self.timezone = pytz.timezone(self.config.get('timezone', 'UTC'))
        
        # Threading
        self.running = True
        self.display_lock = threading.Lock()
        
        # NTP sync
        self.last_ntp_sync = 0
        self.ntp_sync_interval = 3600  # 1 hour
        
        self.logger.info("NanoPi OLED Monitor initialized")

    def load_config(self):
        """Load configuration from file"""
        default_config = {
            'timezone': 'UTC',
            'display_brightness': 255,
            'auto_brightness': True,
            'ntp_servers': ['pool.ntp.org', 'time.google.com'],
            'display_timeout': 0,  # 0 = never timeout
            'refresh_rate': 1.0
        }
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    default_config.update(config)
            except Exception as e:
                self.logger.warning(f"Could not load config: {e}")
        
        return default_config

    def save_config(self):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            self.logger.error(f"Could not save config: {e}")

    def setup_gpio(self):
        """Setup GPIO for buttons"""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            for pin in self.button_pins:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                GPIO.add_event_detect(
                    pin, GPIO.FALLING, 
                    callback=self.button_callback, 
                    bouncetime=200
                )
            
            self.logger.info("GPIO setup completed")
        except Exception as e:
            self.logger.warning(f"GPIO setup failed: {e}")

    def setup_display(self):
        """Setup OLED display"""
        try:
            # Try different I2C addresses
            addresses = [0x3C, 0x3D]
            self.device = None
            
            for addr in addresses:
                try:
                    serial = i2c(port=1, address=addr)
                    self.device = ssd1306(serial, width=128, height=64)
                    self.device.contrast(self.config['display_brightness'])
                    self.logger.info(f"OLED initialized at address 0x{addr:02X}")
                    break
                except Exception as e:
                    continue
            
            if not self.device:
                raise Exception("Could not initialize OLED display")
                
        except Exception as e:
            self.logger.error(f"Display setup failed: {e}")
            # Create a dummy device for testing without hardware
            self.device = None

    def button_callback(self, channel):
        """Handle button press"""
        try:
            if channel == self.button_pins[0]:  # F1 - Change display mode
                self.current_mode = (self.current_mode + 1) % len(self.display_modes)
                self.logger.info(f"Switched to mode: {self.display_modes[self.current_mode]}")
            
            elif channel == self.button_pins[1]:  # F2 - Change timezone
                self.cycle_timezone()
            
            elif channel == self.button_pins[2]:  # F3 - Force NTP sync
                self.sync_ntp()
                
        except Exception as e:
            self.logger.error(f"Button callback error: {e}")

    def cycle_timezone(self):
        """Cycle through common timezones"""
        timezones = [
            'UTC', 'EST', 'PST', 'GMT', 'CET', 'JST', 
            'Asia/Shanghai', 'Europe/London', 'America/New_York',
            'America/Los_Angeles', 'Asia/Tokyo'
        ]
        
        try:
            current_tz = self.config['timezone']
            current_index = timezones.index(current_tz) if current_tz in timezones else 0
            next_index = (current_index + 1) % len(timezones)
            
            self.config['timezone'] = timezones[next_index]
            self.timezone = pytz.timezone(timezones[next_index])
            self.save_config()
            
            self.logger.info(f"Timezone changed to: {timezones[next_index]}")
        except Exception as e:
            self.logger.error(f"Timezone change error: {e}")

    def sync_ntp(self):
        """Synchronize time with NTP servers"""
        try:
            for server in self.config['ntp_servers']:
                try:
                    subprocess.check_call(['sudo', 'ntpdate', '-s', server], 
                                        timeout=10, 
                                        stdout=subprocess.DEVNULL, 
                                        stderr=subprocess.DEVNULL)
                    self.last_ntp_sync = time.time()
                    self.logger.info(f"NTP sync successful with {server}")
                    return True
                except:
                    continue
            
            self.logger.warning("All NTP sync attempts failed")
            return False
            
        except Exception as e:
            self.logger.error(f"NTP sync error: {e}")
            return False

    def get_system_info(self):
        """Get system information"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                'cpu': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used': memory.used // (1024**2),  # MB
                'memory_total': memory.total // (1024**2),  # MB
                'disk_percent': disk.percent,
                'disk_used': disk.used // (1024**3),  # GB
                'disk_total': disk.total // (1024**3)  # GB
            }
        except Exception as e:
            self.logger.error(f"System info error: {e}")
            return None

    def get_network_info(self):
        """Get network information"""
        try:
            # Get IP address
            result = subprocess.run(['hostname', '-I'], 
                                  capture_output=True, text=True, timeout=5)
            ip_addresses = result.stdout.strip().split()
            
            # Get network stats
            net_io = psutil.net_io_counters()
            
            return {
                'ip_addresses': ip_addresses,
                'bytes_sent': net_io.bytes_sent // (1024**2),  # MB
                'bytes_recv': net_io.bytes_recv // (1024**2),  # MB
            }
        except Exception as e:
            self.logger.error(f"Network info error: {e}")
            return None

    def get_temperature(self):
        """Get system temperature"""
        try:
            # Try multiple temperature sources
            temp_files = [
                '/sys/class/thermal/thermal_zone0/temp',
                '/sys/class/hwmon/hwmon0/temp1_input'
            ]
            
            for temp_file in temp_files:
                try:
                    with open(temp_file, 'r') as f:
                        temp = int(f.read().strip()) / 1000.0
                        return temp
                except:
                    continue
            
            # Fallback to psutil if available
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        return entries[0].current
            
            return None
            
        except Exception as e:
            self.logger.error(f"Temperature reading error: {e}")
            return None

    def draw_datetime(self, draw, width, height):
        """Draw date and time display"""
        try:
            now = datetime.now(self.timezone)
            
            # Date
            date_str = now.strftime("%a, %b %d %Y")
            
            # Time
            time_str = now.strftime("%H:%M:%S")
            
            # Timezone
            tz_str = str(self.timezone).split('/')[-1]
            
            # Draw text
            draw.text((0, 0), date_str, fill="white")
            draw.text((0, 20), time_str, fill="white")
            draw.text((0, 40), f"TZ: {tz_str}", fill="white")
            
        except Exception as e:
            draw.text((0, 0), f"Time Error: {str(e)[:15]}", fill="white")

    def draw_system_info(self, draw, width, height):
        """Draw system information"""
        try:
            info = self.get_system_info()
            if not info:
                draw.text((0, 0), "System info unavailable", fill="white")
                return
            
            draw.text((0, 0), f"CPU: {info['cpu']:.1f}%", fill="white")
            draw.text((0, 12), f"RAM: {info['memory_percent']:.1f}%", fill="white")
            draw.text((0, 24), f"     {info['memory_used']}MB/{info['memory_total']}MB", fill="white")
            draw.text((0, 36), f"Disk: {info['disk_percent']:.1f}%", fill="white")
            draw.text((0, 48), f"      {info['disk_used']}GB/{info['disk_total']}GB", fill="white")
            
        except Exception as e:
            draw.text((0, 0), f"Sys Error: {str(e)[:15]}", fill="white")

    def draw_network_info(self, draw, width, height):
        """Draw network information"""
        try:
            info = self.get_network_info()
            if not info:
                draw.text((0, 0), "Network info unavailable", fill="white")
                return
            
            draw.text((0, 0), "Network Info", fill="white")
            
            y_pos = 12
            for ip in info['ip_addresses'][:2]:  # Show max 2 IPs
                draw.text((0, y_pos), f"IP: {ip}", fill="white")
                y_pos += 12
            
            draw.text((0, y_pos), f"TX: {info['bytes_sent']}MB", fill="white")
            draw.text((0, y_pos + 12), f"RX: {info['bytes_recv']}MB", fill="white")
            
        except Exception as e:
            draw.text((0, 0), f"Net Error: {str(e)[:15]}", fill="white")

    def draw_temperature(self, draw, width, height):
        """Draw temperature information"""
        try:
            temp = self.get_temperature()
            
            draw.text((0, 0), "Temperature", fill="white")
            
            if temp is not None:
                draw.text((0, 20), f"CPU: {temp:.1f}°C", fill="white")
                
                # Temperature status
                if temp < 50:
                    status = "COOL"
                elif temp < 70:
                    status = "WARM"
                else:
                    status = "HOT!"
                
                draw.text((0, 40), f"Status: {status}", fill="white")
            else:
                draw.text((0, 20), "Temperature sensor", fill="white")
                draw.text((0, 32), "not available", fill="white")
                
        except Exception as e:
            draw.text((0, 0), f"Temp Error: {str(e)[:15]}", fill="white")

    def update_display(self):
        """Update the OLED display"""
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
                    
        except Exception as e:
            self.logger.error(f"Display update error: {e}")

    def auto_ntp_sync(self):
        """Automatically sync NTP if needed"""
        if time.time() - self.last_ntp_sync > self.ntp_sync_interval:
            self.sync_ntp()

    def display_thread(self):
        """Main display update thread"""
        while self.running:
            try:
                self.auto_ntp_sync()
                self.update_display()
                time.sleep(self.config['refresh_rate'])
            except Exception as e:
                self.logger.error(f"Display thread error: {e}")
                time.sleep(5)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info("Shutdown signal received")
        self.running = False

    def run(self):
        """Main run method"""
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Initial NTP sync
        self.sync_ntp()
        
        # Start display thread
        display_thread = threading.Thread(target=self.display_thread)
        display_thread.daemon = True
        display_thread.start()
        
        self.logger.info("NanoPi OLED Monitor started")
        
        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()

    def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Cleaning up...")
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
        
        self.logger.info("Cleanup completed")

def create_systemd_service():
    """Create systemd service for auto-start"""
    service_content = f"""[Unit]
Description=NanoPi OLED Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory={Path.home()}
ExecStart={sys.executable} {__file__}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    
    service_path = Path('/etc/systemd/system/nanopi-oled-monitor.service')
    
    try:
        with open(service_path, 'w') as f:
            f.write(service_content)
        
        subprocess.run(['systemctl', 'daemon-reload'])
        subprocess.run(['systemctl', 'enable', 'nanopi-oled-monitor.service'])
        
        print("Systemd service created and enabled")
        print("Use 'sudo systemctl start nanopi-oled-monitor' to start")
        print("Use 'sudo systemctl status nanopi-oled-monitor' to check status")
        
    except Exception as e:
        print(f"Failed to create systemd service: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--install-service':
        create_systemd_service()
        sys.exit(0)
    
    monitor = NanoPiOLEDMonitor()
    monitor.run()