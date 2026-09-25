"""Record the ESP32-CAM MJPEG stream for offline replay and tuning (no detection).

Every frame is saved exactly as the camera sent it (no re-encoding) together with its
arrival time, so `python -m csd --source <folder>` replays with the real frame timing
(blink detection, TTC and smoothing depend on it). Folder layout: csd/recorder.py.

Usage:
  python tools/record.py                         # until Ctrl+C
  python tools/record.py --seconds 600 --name daytime
  python tools/record.py --no-settings           # keep whatever the camera is set to
  python -m csd --source recordings/<folder> --show

To record while the detector runs (e.g. during a demo), use `python -m csd --record`.
Recordings contain licence plates and faces: they stay local (git-ignored).
"""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from csd import config as cfgmod  # noqa: E402
from csd.camera_ctl import apply_settings  # noqa: E402
from csd.discover import find_camera, stream_url  # noqa: E402
from csd.recorder import Recorder  # noqa: E402
from csd.stream import iter_mjpeg  # noqa: E402


def record_usb(port: str, cam: dict, a) -> int:
    """Record over the USB cable (csd/usb_camera.py); same folder layout as Wi-Fi recordings."""
    from csd.usb_camera import UsbCamStream

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder.new_session(out, a.name, a.max_mb)
    rec.meta.update({"source": f"usb:{port}", "camera_settings": None if a.no_settings else cam["settings"]})
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    src = UsbCamStream(port, on_jpeg=rec.add).start()
    if not a.no_settings:
        src.apply_settings(cam["settings"])
    print(f"recording USB {port} -> {rec.folder}  (Ctrl+C to stop)")
    t_start, last_print, last_frames, reason = time.time(), time.time(), 0, "stopped by user"
    try:
        while not stop.wait(0.5):
            now = time.time()
            if rec.full:
                reason = f"size limit {a.max_mb:.0f} MB reached"
                break
            if a.seconds and now - t_start >= a.seconds:
                reason = f"{a.seconds:.0f} s elapsed"
                break
            if now - last_print >= 5:
                fps = (rec.frames - last_frames) / (now - last_print)
                print(f"  {now - t_start:6.0f}s  frames {rec.frames:6d}  {fps:4.1f} fps  "
                      f"{rec.bytes / 1e6:7.1f} MB", flush=True)
                last_print, last_frames = now, rec.frames
    finally:
        src.stop()
        meta = rec.close(stop_reason=reason, camera_status=src.status)
        print(f"saved {rec.frames} frames, {rec.duration:.1f} s, {rec.bytes / 1e6:.1f} MB, "
              f"{meta['avg_fps']} fps -> {rec.folder}  ({reason})")
        print(f"replay: python -m csd --source \"{rec.folder}\" --show")
    return 0 if rec.frames else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Record the ESP32-CAM MJPEG stream")
    ap.add_argument("--config", help="YAML overriding configs/default.yaml (camera URLs/settings)")
    ap.add_argument("--url", help="stream URL (default: find the camera automatically)")
    ap.add_argument("--out", default=str(ROOT / "recordings"), help="parent folder for recordings")
    ap.add_argument("--name", default="", help="label appended to the folder name, e.g. daytime")
    ap.add_argument("--seconds", type=float, help="stop after N seconds (default: until Ctrl+C)")
    ap.add_argument("--max-mb", type=float, default=4000, help="stop when the recording reaches this size")
    ap.add_argument("--no-settings", action="store_true", help="do not send camera settings before recording")
    a = ap.parse_args()

    cfg = cfgmod.load(a.config)
    cam = cfg["camera"]
    url, status = a.url, {}
    if not url and cam.get("transport", "auto") in ("auto", "usb"):
        from csd.usb_camera import find_usb_camera
        port = find_usb_camera()
        if port:
            return record_usb(port, cam, a)
        if cam.get("transport") == "usb":
            print("USB camera not found (connect the board's native USB port)")
            return 1
    if not url:
        base = find_camera(cam["base_url"])
        if not base:
            print("camera not found (configured address, 192.168.4.1, esp32cam.local, local subnet scan)")
            return 1
        url = stream_url(base)
        if not a.no_settings:
            status = apply_settings(base, cam["settings"])

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    free_mb = shutil.disk_usage(out).free / 1e6
    if free_mb < a.max_mb + 500:
        a.max_mb = max(100.0, free_mb - 500)
        print(f"low disk space: limiting this recording to {a.max_mb:.0f} MB")

    rec = Recorder.new_session(out, a.name, a.max_mb)
    rec.meta.update({"url": url, "camera_status": status,
                     "camera_settings": None if a.no_settings else cam["settings"]})
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    t_start = time.time()
    last_print, last_frames = t_start, 0
    reason = "stopped by user"
    print(f"recording {url} -> {rec.folder}  (Ctrl+C to stop)")
    try:
        while not stop.is_set():
            try:
                for t, jpg in iter_mjpeg(url, timeout=5.0, stop=stop):
                    if not rec.add(t, jpg):
                        reason = f"size limit {a.max_mb:.0f} MB reached"
                        stop.set()
                        break
                    if a.seconds and t - t_start >= a.seconds:
                        reason = f"{a.seconds:.0f} s elapsed"
                        stop.set()
                        break
                    now = time.time()
                    if now - last_print >= 5:
                        fps = (rec.frames - last_frames) / (now - last_print)
                        print(f"  {now - t_start:6.0f}s  frames {rec.frames:6d}  {fps:4.1f} fps  "
                              f"{rec.bytes / 1e6:7.1f} MB", flush=True)
                        last_print, last_frames = now, rec.frames
            except Exception as e:  # Wi-Fi drop, camera reboot: keep the same folder
                if stop.is_set():
                    break
                rec.meta["reconnects"] += 1
                rec.meta["gaps"].append({"at_rel_s": round(rec.duration, 3), "error": str(e)[:200]})
                print(f"  stream error ({e}); reconnecting in 2 s", flush=True)
                stop.wait(2.0)
    finally:
        meta = rec.close(stop_reason=reason)
        print(f"saved {rec.frames} frames, {rec.duration:.1f} s, {rec.bytes / 1e6:.1f} MB, "
              f"{meta['avg_fps']} fps -> {rec.folder}  ({reason})")
        print(f"replay: python -m csd --source \"{rec.folder}\" --show")
    return 0 if rec.frames else 1


if __name__ == "__main__":
    sys.exit(main())
