# rpiz2w-hid

Raspberry Pi Zero 2 W as a **network → USB HID proxy**. The Pi plugs into a
host computer through its USB OTG port and shows up as a **USB keyboard +
absolute mouse**. It listens on **TCP port 5555** and turns the text commands
it receives into real keystrokes and mouse input on the host.

```
 client ──TCP:5555──▶ Pi Zero 2 W ──USB HID (OTG)──▶ host computer
```

It speaks the same text protocol as the companion ESP32-S3 firmware
(esp32-virtual-hid), so the bundled client `tools/hidproxy.py` works with
either device.

Minimal first version: Python 3 standard library only, US keyboard layout,
absolute mouse, one client at a time.

## Hardware

The Zero 2 W has two micro-USB ports:

* **`USB`** (the one closer to the middle of the board) is the OTG data port.
  Connect it to the host computer with a **data** cable (not a charge-only
  cable). The host can power the Pi through this port.
* **`PWR IN`** is power only. Optional if the host supplies enough power.

Also works on Pi Zero / Zero W, Pi 4 (USB-C port) and Pi 5 (USB-C port).

## Install

On Raspberry Pi OS (tested on Trixie Lite 64-bit, 2026-10-06):

```sh
# on the Pi
git clone https://github.com/heydemoura/rpiz2w-hid.git
cd rpiz2w-hid && sudo ./install.sh && sudo reboot
```

(No git on the Pi? From your computer: `rsync -a rpiz2w-hid/ pi@rpiz2w-hid.local:rpiz2w-hid/`.)

`install.sh` does the following:

1. Adds `dtoverlay=dwc2,dr_mode=peripheral` to `/boot/firmware/config.txt`
   (puts the OTG port in device mode) and loads `dwc2` + `libcomposite` at boot.
2. Copies the code to `/opt/hidproxy` and creates `/etc/default/hidproxy`
   (port and auth token).
3. Enables two systemd services:
   * `hid-gadget.service` runs `gadget/hid-gadget.sh up`, which creates the
     configfs USB gadget: `/dev/hidg0` (keyboard) and `/dev/hidg1` (mouse).
   * `hidproxy.service` runs the TCP server (`python3 -m hidproxy`).
4. Advertises `_hidproxy._tcp` over mDNS (avahi).

Both services are enabled, so after the reboot (and every boot after that) the
proxy starts automatically, about 30 s after power-on on a Zero 2 W. There's no
need to log in. If the host is plugged in at boot, it enumerates the keyboard and
mouse as soon as the gadget is bound.

Check it's running:

```sh
systemctl status hid-gadget hidproxy
cat /sys/class/udc/*/state      # "configured" once a host has enumerated the Pi
journalctl -u hidproxy -f
```

## Protocol

Plain text over TCP, one command per line (`\n`). Each command gets exactly
one response line: `OK`, `OK <data>` or `ERR <reason>`. Commands are
case-insensitive.

| Command | Effect |
|---|---|
| `AUTH <token>` | Required first if a token is configured |
| `PING` | → `OK PONG` |
| `HELP` | List commands |
| `TYPE <text>` | Type text (printable ASCII; escapes `\n` `\t` `\\`) |
| `KEY <combo> [combo…]` | Tap key combos in order, e.g. `KEY ctrl+c`, `KEY cmd+space`, `KEY enter` |
| `MOVETO <x> <y>` | Move pointer to absolute pixel position → `OK <x> <y>` |
| `MOVE <dx> <dy>` | Move relative to the last position → `OK <x> <y>` |
| `CLICK [left\|right\|middle] [count]` | Click at the current position (count 1–10) |
| `SCROLL <n>` | Scroll wheel by n notches (positive = up) |
| `SCREEN <w> <h>` | Host screen size used to map pixels (default 1920×1080) |
| `DELAY <ms>` | Wait 0–10000 ms |

Key names: `ctrl shift alt/option cmd/win/gui/super/meta`, `rctrl rshift ralt
rgui`, `enter esc backspace tab space delete insert home end pageup pagedown
up down left right capslock printscreen menu f1`–`f12`, `plus minus`, or any
single printable character.

Notes:

* HID commands return `ERR usb not connected to host` until the host has
  enumerated the gadget. `PING`, `SCREEN` and `DELAY` always work.
* The mouse uses **absolute** coordinates: `MOVETO` maps pixels onto the whole
  screen using `SCREEN`. On HiDPI/Retina displays, use logical (point) sizes.
* `TYPE` assumes the host's keyboard layout is **US**.
* A new connection replaces the current one. Keys are released when a client
  disconnects.

### Try it

```sh
nc rpiz2w-hid.local 5555
PING
OK PONG
TYPE Hello from the network!\n
OK
```

```sh
python3 tools/hidproxy.py --host rpiz2w-hid.local \
    "SCREEN 1920 1080" "MOVETO 960 540" "CLICK" "TYPE hello\n"
```

On macOS the first connection may open the *Keyboard Setup Assistant*. Close
it or pick ANSI.

## Configuration

`/etc/default/hidproxy`:

```sh
HIDPROXY_PORT=5555
HIDPROXY_TOKEN=mysecret   # clients must send "AUTH mysecret" first
```

Then `sudo systemctl restart hidproxy`. If you change the port, also update
`/etc/avahi/services/hidproxy.service`.

## Security

Anyone who can reach the port can type and click on the host. Set a token and
keep the Pi on a trusted network. The token travels in plaintext. The server
runs as root because it needs `/dev/hidg*`.

## Development

```sh
python3 -m unittest discover -s tests       # parser + TCP server tests (no Pi needed)
python3 -m hidproxy --dry-run --port 5555   # run locally, logs HID reports instead of sending
```

## Layout

```
hidproxy/commands.py   protocol parser → HID keyboard/mouse reports (hardware independent)
hidproxy/hid.py        /dev/hidgN writer (GadgetBackend) and a dry-run logger
hidproxy/server.py     TCP server, auth, CLI options
gadget/hid-gadget.sh   configfs USB gadget: HID report descriptors, bind to UDC
systemd/               service units, defaults file, avahi service
install.sh             one-shot installer for Raspberry Pi OS
tests/                 unittest suite
tools/hidproxy.py      Python client / CLI (stdlib only)
```
