"""TCP server: accepts protocol lines and feeds them to Commands."""
import argparse
import hmac
import logging
import os
import socket
import threading

from .commands import Commands
from .hid import DryRunBackend, GadgetBackend, HidWriteError

log = logging.getLogger("hidproxy")
MAX_LINE = 2048


class Server:
    def __init__(self, backend, token=None, max_line=MAX_LINE):
        self.backend = backend
        self.commands = Commands(backend)
        self.token = token or None
        self.max_line = max_line
        self._exec_lock = threading.Lock()   # one command at a time hits the HID
        self._client_lock = threading.Lock()
        self._current = None

    def serve_forever(self, bind="0.0.0.0", port=5555):
        srv = socket.create_server((bind, port))
        log.info("listening on %s:%d (auth %s)", bind, port, "on" if self.token else "off")
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._client_lock:
                old, self._current = self._current, conn
            if old is not None:
                # Newest connection wins so a dead/stuck client can't lock others out.
                log.info("replacing existing client")
                _shutdown(old)
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def handle_line(self, line, state):
        """Process one line; returns the response. `state` holds per-client auth."""
        verb, _, arg = line.strip().partition(" ")
        if verb.lower() == "auth":
            if not self.token or state["authed"]:
                return "OK"  # harmless no-op, like the ESP32 firmware
            if hmac.compare_digest(arg.strip().encode(), self.token.encode()):
                state["authed"] = True
                return "OK"
            return "ERR bad token"
        if self.token and not state["authed"]:
            return "ERR auth required: send AUTH <token>"
        with self._exec_lock:
            try:
                return self.commands.execute(line)
            except (HidWriteError, OSError) as e:
                log.warning("hid error: %s", e)
                try:
                    self.backend.release_all()
                except Exception:
                    pass
                return f"ERR {e}"

    def _handle(self, conn, addr):
        log.info("client %s:%d connected", *addr[:2])
        state = {"authed": False}
        buf, overflow = b"", False
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if overflow:
                        resp, overflow = "ERR line too long", False
                    elif len(raw) > self.max_line:
                        resp = "ERR line too long"
                    else:
                        resp = self.handle_line(raw.decode("utf-8", "replace"), state)
                    conn.sendall(resp.encode("utf-8") + b"\n")
                if len(buf) > self.max_line:
                    buf, overflow = b"", True
        except OSError:
            pass
        finally:
            with self._client_lock:
                if self._current is conn:
                    self._current = None
            with self._exec_lock:
                self.backend.release_all()
            conn.close()
            log.info("client %s:%d disconnected", *addr[:2])


def _shutdown(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Network -> USB HID proxy for Raspberry Pi OTG")
    ap.add_argument("--bind", default=os.environ.get("HIDPROXY_BIND", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HIDPROXY_PORT", "5555")))
    ap.add_argument("--token", default=os.environ.get("HIDPROXY_TOKEN") or None,
                    help="require 'AUTH <token>' before other commands")
    ap.add_argument("--keyboard-dev", default="/dev/hidg0")
    ap.add_argument("--mouse-dev", default="/dev/hidg1")
    ap.add_argument("--dry-run", action="store_true", help="log HID reports instead of sending them")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose or args.dry_run else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    backend = DryRunBackend() if args.dry_run else GadgetBackend(args.keyboard_dev, args.mouse_dev)
    try:
        Server(backend, token=args.token).serve_forever(args.bind, args.port)
    except KeyboardInterrupt:
        pass
