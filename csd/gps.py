"""Position and speed from a phone's GPS (NMEA 0183).

The laptop has no GPS receiver; a phone app streams NMEA sentences instead:
  tcp://HOST:PORT     phone app as a TCP NMEA server (Android "Share GPS": port 50000,
                      iOS "GPS2IP": 11123). With USB tethering the phone is the laptop's
                      default gateway, so HOST may be written as "gateway".
  serial://COM8:9600  Bluetooth SPP / USB serial NMEA port
  file://path.nmea    replay of a saved NMEA log (one sentence per line)
Only $--RMC is needed (position, speed over ground, course, validity); $--GGA adds the
satellite count for the log.
"""

from __future__ import annotations

import logging
import math
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

KNOT_KMH = 1.852


@dataclass
class Fix:
    t: float               # arrival time (unix, laptop clock)
    lat: float
    lon: float
    speed_kmh: float
    course: float | None   # degrees from north, None when not moving / not reported
    sats: int | None = None


def checksum_ok(sentence: str) -> bool:
    """`$...*hh` with a valid XOR checksum (sentences without one are accepted)."""
    s = sentence.strip()
    if not s.startswith("$"):
        return False
    if "*" not in s:
        return True
    body, _, cs = s[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(cs[:2], 16)
    except ValueError:
        return False


def _deg(value: str, hemi: str) -> float:
    """NMEA ddmm.mmmm / dddmm.mmmm -> signed decimal degrees."""
    v = float(value)
    deg = int(v // 100)
    out = deg + (v - deg * 100) / 60
    return -out if hemi in ("S", "W") else out


def parse_rmc(sentence: str, t: float) -> Fix | None:
    """A valid $GPRMC / $GNRMC (any talker) -> Fix; None for void or malformed ones."""
    if not checksum_ok(sentence):
        return None
    f = sentence.strip().split("*")[0].split(",")
    if len(f) < 9 or not re.fullmatch(r"\$..RMC", f[0]) or f[2] != "A":
        return None
    try:
        lat, lon = _deg(f[3], f[4]), _deg(f[5], f[6])
        speed = float(f[7] or 0) * KNOT_KMH
        course = float(f[8]) if f[8] else None
    except ValueError:
        return None
    return Fix(t, lat, lon, speed, course)


def parse_gga_sats(sentence: str) -> int | None:
    f = sentence.strip().split("*")[0].split(",")
    if len(f) > 7 and re.fullmatch(r"\$..GGA", f[0]) and checksum_ok(sentence):
        try:
            return int(f[7])
        except ValueError:
            return None
    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def default_gateways() -> list[str]:
    """IPv4 default gateways (a USB-tethered phone is one of them)."""
    try:
        out = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return []
    gws = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", parts[2]) and parts[2] not in gws:
                gws.append(parts[2])
    return gws


class GpsSource:
    """Background reader keeping the latest fix; reconnects on errors."""

    def __init__(self, url: str, stale_s: float = 3.0, reconnect_s: float = 3.0, on_fix=None):
        self.url = url
        self.stale_s = stale_s
        self.reconnect_s = reconnect_s
        self.on_fix = on_fix       # called with every new Fix (e.g. to save gps.csv)
        self.connected = False
        self.where = ""
        self._fix: Fix | None = None
        self._sats: int | None = None
        self._prev: Fix | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gps", daemon=True)

    def start(self) -> "GpsSource":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)

    def latest(self, now: float | None = None) -> Fix | None:
        """The current fix, or None when there is none or it is older than `stale_s`."""
        now = time.time() if now is None else now
        with self._lock:
            fix = self._fix
        return fix if fix is not None and now - fix.t <= self.stale_s else None

    # ------------------------------------------------------------------ input
    def feed_line(self, line: str, t: float | None = None) -> Fix | None:
        t = time.time() if t is None else t
        sats = parse_gga_sats(line)
        if sats is not None:
            self._sats = sats
            return None
        fix = parse_rmc(line, t)
        if fix is None:
            return None
        fix.sats = self._sats
        prev = self._prev
        if fix.course is None and prev is not None and fix.speed_kmh >= 5:
            # Some apps leave the course empty: take it from the last two positions.
            if haversine_m(prev.lat, prev.lon, fix.lat, fix.lon) >= 3:
                fix.course = bearing_deg(prev.lat, prev.lon, fix.lat, fix.lon)
        self._prev = fix
        with self._lock:
            self._fix = fix
        if self.on_fix is not None:
            self.on_fix(fix)
        return fix

    def _lines(self):
        scheme, _, rest = self.url.partition("://")
        if scheme == "tcp":
            host, _, port = rest.rpartition(":")
            hosts = default_gateways() if host == "gateway" else [host]
            last_err: Exception | None = None
            for h in hosts:
                try:
                    sock = socket.create_connection((h, int(port)), timeout=3)
                except OSError as e:
                    last_err = e
                    continue
                self.where = f"{h}:{port}"
                sock.settimeout(5)
                yield from _socket_lines(sock, self._stop)
                return
            raise ConnectionError(f"no NMEA server at {rest} ({last_err})")
        if scheme == "serial":
            import serial
            port, _, baud = rest.partition(":")
            with serial.Serial(port, int(baud or 9600), timeout=2) as s:
                self.where = port
                while not self._stop.is_set():
                    raw = s.readline()
                    if raw:
                        yield raw.decode("ascii", "ignore")
            return
        if scheme == "file":
            self.where = rest
            with open(rest, encoding="ascii", errors="ignore") as f:
                for line in f:
                    if self._stop.is_set():
                        return
                    yield line
                    if parse_rmc(line, 0) is not None:
                        self._stop.wait(1.0)  # NMEA logs are 1 Hz
            return
        raise ValueError(f"unknown GPS source: {self.url}")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for line in self._lines():
                    self.connected = True
                    self.feed_line(line)
                if self.url.startswith("file://"):
                    return
            except Exception as e:
                log.warning("GPS %s: %s; retrying in %.0fs", self.url, e, self.reconnect_s)
            self.connected = False
            self._stop.wait(self.reconnect_s)


def _socket_lines(sock: socket.socket, stop: threading.Event):
    buf = b""
    with sock:
        while not stop.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                raise ConnectionError("no NMEA data for 5 s")
            if not data:
                raise ConnectionError("closed by phone")
            buf += data
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                yield raw.decode("ascii", "ignore")


class GpsLog:
    """gps.csv next to a recording, so drives can be replayed with their positions."""

    def __init__(self, folder: Path):
        self._f = open(Path(folder) / "gps.csv", "w", encoding="utf-8", newline="\n")
        self._f.write("t_unix,lat,lon,speed_kmh,course,sats\n")
        self._lock = threading.Lock()

    def __call__(self, fix: Fix) -> None:
        with self._lock:
            if self._f.closed:
                return
            course = "" if fix.course is None else f"{fix.course:.1f}"
            sats = "" if fix.sats is None else str(fix.sats)
            self._f.write(f"{fix.t:.3f},{fix.lat:.7f},{fix.lon:.7f},{fix.speed_kmh:.1f},{course},{sats}\n")
            self._f.flush()

    def close(self) -> None:
        with self._lock:
            self._f.close()
