#!/bin/bash
# Install the HID proxy on a Raspberry Pi (Zero / Zero 2 W / 4 / 5) running Raspberry Pi OS.
# Run from the project directory:  sudo ./install.sh
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
cd "$(dirname "$0")"

CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
reboot_needed=0

# 1. Put the OTG controller in peripheral (device) mode.
if ! grep -q '^dtoverlay=dwc2,dr_mode=peripheral' "$CONFIG"; then
    printf '\n[all]\n# hidproxy: USB OTG port in device mode\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$CONFIG"
    echo "added dtoverlay=dwc2,dr_mode=peripheral to $CONFIG"
    reboot_needed=1
fi
printf 'dwc2\nlibcomposite\n' > /etc/modules-load.d/hidproxy.conf

# 2. Code and config.
rm -rf /opt/hidproxy
mkdir -p /opt/hidproxy
cp -r hidproxy gadget /opt/hidproxy/
chmod +x /opt/hidproxy/gadget/hid-gadget.sh
[ -f /etc/default/hidproxy ] || install -m 600 systemd/hidproxy.default /etc/default/hidproxy

# 3. Services (+ mDNS advertisement if avahi is installed).
install -m 644 systemd/hid-gadget.service systemd/hidproxy.service /etc/systemd/system/
if [ -d /etc/avahi/services ]; then
    install -m 644 systemd/hidproxy.avahi.xml /etc/avahi/services/hidproxy.service
fi
systemctl daemon-reload
systemctl enable hid-gadget.service hidproxy.service

if [ "$reboot_needed" -eq 1 ] || [ -z "$(ls /sys/class/udc 2>/dev/null)" ]; then
    echo "Installed. Reboot to enable the USB device controller: sudo reboot"
else
    systemctl restart hid-gadget.service hidproxy.service
    echo "Installed and running on port $(. /etc/default/hidproxy; echo "${HIDPROXY_PORT:-5555}")."
fi
