"""Find the ESP32-CAM on whatever network the laptop is on.

Order: configured address -> camera's own access point (192.168.4.1, SSID CSD-CAM) ->
mDNS name esp32cam.local -> scan of the laptop's /24 subnets (phone hotspot case, where
the camera gets an unknown DHCP address). A camera is recognised by CameraWebServer's
/status JSON (it contains "framesize").
"""

from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import logging
import socket

import requests

log = logging.getLogger(__name__)

AP_URL = "http://192.168.4.1"
MDNS_URL = "http://esp32cam.local"


def is_camera(base_url: str, timeout: float = 1.5) -> bool:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/status", timeout=timeout)
        return r.status_code == 200 and "framesize" in r.json()
    except (requests.RequestException, ValueError):
        return False


def local_subnets() -> list[ipaddress.IPv4Network]:
    """/24 networks of this PC's IPv4 addresses (loopback and link-local excluded)."""
    nets = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = ipaddress.IPv4Address(info[4][0])
            if not (ip.is_loopback or ip.is_link_local):
                nets.add(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    except OSError:
        pass
    return sorted(nets)


def scan(nets: list[ipaddress.IPv4Network], timeout: float = 0.6, workers: int = 64) -> str | None:
    hosts = [f"http://{h}" for n in nets for h in n.hosts()]
    with cf.ThreadPoolExecutor(workers) as ex:
        futures = {ex.submit(is_camera, h, timeout): h for h in hosts}
        for f in cf.as_completed(futures):
            if f.result():
                for other in futures:
                    other.cancel()
                return futures[f]
    return None


def find_camera(configured: str | None, allow_scan: bool = True) -> str | None:
    """Base URL (e.g. http://192.168.4.1) of a reachable camera, or None."""
    for url in [u for u in (configured, AP_URL, MDNS_URL) if u]:
        if is_camera(url):
            log.info("camera found at %s", url)
            return url.rstrip("/")
    if allow_scan:
        nets = local_subnets()
        log.info("scanning %s for the camera...", ", ".join(map(str, nets)) or "no networks")
        url = scan(nets)
        if url:
            log.info("camera found at %s", url)
            return url
    return None


def stream_url(base_url: str) -> str:
    """CameraWebServer serves the MJPEG stream on port 81."""
    host = base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return f"http://{host}:81/stream"
