"""ESP32-CAM frames over the USB cable (the board's native USB port, firmware usb_stream.cpp).

The laptop's Wi-Fi stays free for the internet. Packets from the camera:
  4-byte type ("CSDF" = JPEG frame, "CSDJ" = status JSON), uint32 LE length,
  uint32 LE camera millis, payload.
Commands to the camera: "S" start, "X" stop, "Q" status, "C <var> <val>" set a sensor value.

UsbCamStream has the same interface as stream.MjpegStream (start/read/stop, on_jpeg,
connected, received), so the pipeline, recorder and voice labelling work unchanged.
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

ESPRESSIF_VID = 0x303A
USB_JTAG_PID = 0x1001          # ESP32-S3 USB-Serial/JTAG (our firmware's native USB port)
MAGIC = b"CSD"
HEADER = struct.Struct("<4sII")  # type, length, camera millis
MAX_PAYLOAD = 2_000_000


class PacketParser:
    """Incremental parser; resynchronises on the magic after garbage or a lost byte."""

    def __init__(self):
        self.buf = bytearray()
        self.skipped = 0

    def feed(self, data: bytes) -> list[tuple[bytes, int, bytes]]:
        """Returns complete packets as (type, camera_ms, payload)."""
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                self.skipped += max(0, len(self.buf) - 2)
                del self.buf[:-2]  # the magic may be split across reads
                return out
            if i:
                self.skipped += i
                del self.buf[:i]
            if len(self.buf) < HEADER.size:
                return out
            kind, length, ms = HEADER.unpack_from(self.buf)
            if kind not in (b"CSDF", b"CSDJ") or length > MAX_PAYLOAD:
                self.skipped += 1
                del self.buf[:1]
                continue
            end = HEADER.size + length
            if len(self.buf) < end:
                return out
            payload = bytes(self.buf[HEADER.size:end])
            if kind == b"CSDF":
                valid = payload[:2] == b"\xff\xd8" and payload[-2:] == b"\xff\xd9"
            else:
                valid = payload[:1] == b"{" and payload[-1:] == b"}"
            if not valid:
                # A truncated packet makes its declared length swallow the start of the next
                # packet. Treat this header as false and search again from the next byte.
                self.skipped += 1
                del self.buf[:1]
                continue
            del self.buf[:end]
            out.append((kind, ms, payload))


def list_candidate_ports() -> list[str]:
    from serial.tools import list_ports

    return [p.device for p in list_ports.comports() if p.vid == ESPRESSIF_VID and p.pid == USB_JTAG_PID]


def _open(port: str):
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 2_000_000, 0.05
    # DTR/RTS changes can reset an ESP32-S3 through its USB-Serial/JTAG: keep both off.
    s.dtr, s.rts = False, False
    s.open()
    return s


def query_status(port: str, timeout: float = 2.0) -> dict | None:
    """Camera status JSON if `port` is our camera firmware, else None."""
    try:
        s = _open(port)
    except Exception:
        return None
    try:
        s.write(b"X\nQ\n")
        parser, deadline = PacketParser(), time.time() + timeout
        while time.time() < deadline:
            for kind, _, payload in parser.feed(s.read(4096)):
                if kind == b"CSDJ":
                    return json.loads(payload)
        return None
    except Exception:
        return None
    finally:
        s.close()


def find_usb_camera(attempts: int = 3) -> str | None:
    """Port of a camera running our firmware. Retries because right after another program
    released the port (or while the camera is still flushing a stream) the first query
    can fail."""
    for i in range(attempts):
        for port in list_candidate_ports():
            if query_status(port) is not None:
                log.info("USB camera found on %s", port)
                return port
        if not list_candidate_ports():
            return None  # no Espressif USB port at all: do not wait
        time.sleep(0.7)
    return None


class UsbCamStream:
    def __init__(self, port: str | None = None, reconnect_s: float = 2.0, on_jpeg=None):
        self.port = port
        self.reconnect_s = reconnect_s
        self.on_jpeg = on_jpeg
        self.connected = False
        self.received = 0
        self.status: dict = {}
        self._pending_cmds: list[bytes] = []
        self._frame: np.ndarray | None = None
        self._t = 0.0
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="usbcam", daemon=True)

    def start(self) -> "UsbCamStream":
        self._thread.start()
        return self

    def send(self, cmd: str) -> None:
        with self._lock:
            self._pending_cmds.append((cmd.strip() + "\n").encode())

    def apply_settings(self, settings: dict) -> None:
        from .camera_ctl import FRAMESIZES

        order = ["framesize", "quality"] + [k for k in settings if k not in ("framesize", "quality")]
        for key in order:
            if key in settings:
                val = settings[key]
                if key == "framesize" and isinstance(val, str):
                    val = FRAMESIZES[val.upper()]
                self.send(f"C {key} {int(val)}")

    def _run(self) -> None:
        import cv2

        while not self._stop.is_set():
            port = self.port or (list_candidate_ports() or [None])[0]
            if port is None:
                self._stop.wait(self.reconnect_s)
                continue
            try:
                s = _open(port)
            except Exception as e:
                log.warning("USB camera %s: %s; retrying", port, e)
                self._stop.wait(self.reconnect_s)
                continue
            log.info("USB camera streaming from %s", port)
            parser = PacketParser()
            last_rx = time.time()
            try:
                s.write(b"S\n")
                while not self._stop.is_set():
                    with self._lock:
                        cmds, self._pending_cmds = self._pending_cmds, []
                    for c in cmds:
                        s.write(c)
                    data = s.read(65536)
                    now = time.time()
                    if data:
                        last_rx = now
                    elif now - last_rx > 3.0:
                        s.write(b"S\n")  # camera rebooted or stopped after a stall: ask again
                        last_rx = now
                    for kind, _, payload in parser.feed(data):
                        if kind == b"CSDJ":
                            self.status = json.loads(payload)
                            continue
                        self.received += 1
                        self.connected = True
                        if self.on_jpeg is not None:
                            self.on_jpeg(now, payload)
                        img = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
                        if img is not None:
                            with self._lock:
                                self._frame, self._t = img, now
                                self._seq += 1
                try:
                    s.write(b"X\n")
                except Exception:
                    pass
            except Exception as e:  # cable pulled, camera reset (port re-enumerates)
                self.connected = False
                log.warning("USB camera error (%s); reconnecting", e)
            finally:
                s.close()
            self._stop.wait(self.reconnect_s)

    def read(self, last_seq: int = -1, wait_s: float = 2.0):
        deadline = time.time() + wait_s
        while time.time() < deadline and not self._stop.is_set():
            with self._lock:
                if self._frame is not None and self._seq != last_seq:
                    return self._seq, self._t, self._frame
            time.sleep(0.005)
        return last_seq, 0.0, None

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)
