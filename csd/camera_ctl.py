"""Applies sensor settings to the ESP32-CAM through CameraWebServer's HTTP API
(GET /control?var=<name>&val=<n>, GET /status)."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

# esp32-camera framesize_t values (driver/include/sensor.h in Arduino-ESP32 3.3.12)
FRAMESIZES = {"QVGA": 6, "CIF": 8, "HVGA": 9, "VGA": 10, "SVGA": 11, "XGA": 12, "HD": 13,
              "SXGA": 14, "UXGA": 15, "FHD": 16, "QXGA": 19}


def apply_settings(base_url: str, settings: dict, timeout: float = 3.0) -> dict:
    """Send each setting; returns the camera's /status afterwards (or {} on failure).

    `framesize` may be given as a name (e.g. "XGA") or as the numeric enum value.
    Order matters: resolution first, then exposure/gain/white balance.
    """
    base = base_url.rstrip("/")
    order = ["framesize", "quality"] + [k for k in settings if k not in ("framesize", "quality")]
    for key in order:
        if key not in settings:
            continue
        val = settings[key]
        if key == "framesize" and isinstance(val, str):
            val = FRAMESIZES[val.upper()]
        try:
            r = requests.get(f"{base}/control", params={"var": key, "val": int(val)}, timeout=timeout)
            if r.status_code != 200:
                log.warning("camera setting %s=%s rejected (HTTP %d)", key, val, r.status_code)
        except requests.RequestException as e:
            log.warning("camera setting %s=%s failed: %s", key, val, e)
            return {}
    try:
        return requests.get(f"{base}/status", timeout=timeout).json()
    except (requests.RequestException, ValueError):
        return {}
