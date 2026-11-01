#!/bin/bash
# Create (or remove) a USB HID gadget via configfs: keyboard + absolute mouse.
#   hid-gadget.sh up     -> /dev/hidg0 (keyboard), /dev/hidg1 (mouse)
#   hid-gadget.sh down
# Requires dtoverlay=dwc2 (peripheral mode) and the libcomposite module.
set -euo pipefail

G=/sys/kernel/config/usb_gadget/hidproxy

up() {
    modprobe libcomposite
    mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
    [ -d "$G" ] && down

    mkdir -p "$G"
    cd "$G"
    echo 0x1d6b > idVendor    # Linux Foundation
    echo 0x0104 > idProduct   # Multifunction Composite Gadget
    echo 0x0100 > bcdDevice
    echo 0x0200 > bcdUSB

    mkdir -p strings/0x409
    serial=$(awk '/^Serial/ {print $3}' /proc/cpuinfo)
    echo "${serial:-0000000000000001}" > strings/0x409/serialnumber
    echo "rpiz2w-hid" > strings/0x409/manufacturer
    echo "Pi HID Proxy" > strings/0x409/product

    mkdir -p configs/c.1/strings/0x409
    echo "Keyboard + Mouse" > configs/c.1/strings/0x409/configuration
    echo 100 > configs/c.1/MaxPower

    # Keyboard: standard boot-protocol keyboard, 8-byte reports (+ LED output report).
    mkdir -p functions/hid.keyboard
    echo 1 > functions/hid.keyboard/protocol
    echo 1 > functions/hid.keyboard/subclass
    echo 8 > functions/hid.keyboard/report_length
    printf '%b' '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' \
        > functions/hid.keyboard/report_desc

    # Mouse: absolute pointer. 3 buttons, X/Y 0..32767 (16-bit), wheel -127..127.
    # Report: [buttons, x_lo, x_hi, y_lo, y_hi, wheel]
    mkdir -p functions/hid.mouse
    echo 0 > functions/hid.mouse/protocol
    echo 0 > functions/hid.mouse/subclass
    echo 6 > functions/hid.mouse/report_length
    printf '%b' '\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x15\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06\xc0\xc0' \
        > functions/hid.mouse/report_desc

    ln -s functions/hid.keyboard configs/c.1/
    ln -s functions/hid.mouse configs/c.1/

    udc=$(ls /sys/class/udc | head -n1)
    if [ -z "$udc" ]; then
        echo "no USB device controller found: is dtoverlay=dwc2 set and the Pi rebooted?" >&2
        exit 1
    fi
    echo "$udc" > UDC
    echo "HID gadget bound to $udc"
}

down() {
    [ -d "$G" ] || return 0
    cd "$G"
    [ -n "$(cat UDC 2>/dev/null)" ] && echo "" > UDC
    rm -f configs/c.1/hid.keyboard configs/c.1/hid.mouse
    rmdir configs/c.1/strings/0x409 configs/c.1 2>/dev/null || true
    rmdir functions/hid.keyboard functions/hid.mouse 2>/dev/null || true
    rmdir strings/0x409 2>/dev/null || true
    cd /
    rmdir "$G"
    echo "HID gadget removed"
}

case "${1:-up}" in
    up) up ;;
    down) down ;;
    *) echo "usage: $0 [up|down]" >&2; exit 2 ;;
esac
