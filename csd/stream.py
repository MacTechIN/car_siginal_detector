"""Frame sources: ESP32-CAM MJPEG stream, a video file, or an image folder.

The MJPEG reader runs in its own thread and keeps only the newest decoded frame, so a
slow detector never works on stale images (frames in between are dropped on purpose).
Each frame carries its arrival time, which the temporal logic uses instead of frame counts.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import requests

log = logging.getLogger(__name__)


class MjpegStream:
    def __init__(self, url: str, timeout: float = 5.0, reconnect_s: float = 2.0):
        self.url = url
        self.timeout = timeout
        self.reconnect_s = reconnect_s
        self._frame: np.ndarray | None = None
        self._t = 0.0
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mjpeg", daemon=True)
        self.received = 0

    def start(self) -> "MjpegStream":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with requests.get(self.url, stream=True, timeout=self.timeout) as r:
                    r.raise_for_status()
                    log.info("stream connected: %s", self.url)
                    buf = bytearray()
                    for chunk in r.iter_content(chunk_size=16384):
                        if self._stop.is_set():
                            return
                        buf += chunk
                        # Take the last complete JPEG in the buffer; drop older ones.
                        end = buf.rfind(b"\xff\xd9")
                        if end < 0:
                            if len(buf) > 4_000_000:
                                buf.clear()
                            continue
                        start = buf.rfind(b"\xff\xd8", 0, end)
                        if start < 0:
                            del buf[:end + 2]
                            continue
                        jpg = bytes(buf[start:end + 2])
                        del buf[:end + 2]
                        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                        if img is not None:
                            with self._lock:
                                self._frame, self._t = img, time.time()
                                self._seq += 1
                                self.received += 1
            except Exception as e:
                log.warning("stream error (%s); reconnecting in %.0fs", e, self.reconnect_s)
                self._stop.wait(self.reconnect_s)

    def read(self, last_seq: int = -1, wait_s: float = 2.0) -> tuple[int, float, np.ndarray | None]:
        """Newest frame newer than `last_seq` -> (seq, time, frame); frame None on timeout."""
        deadline = time.time() + wait_s
        while time.time() < deadline and not self._stop.is_set():
            with self._lock:
                if self._frame is not None and self._seq != last_seq:
                    return self._seq, self._t, self._frame
            time.sleep(0.005)
        return last_seq, 0.0, None

    def stop(self) -> None:
        self._stop.set()


class FileSource:
    """Video file or folder of images, replayed with their own timing (for tests/tuning)."""

    def __init__(self, path: str, fps: float = 10.0, realtime: bool = False):
        self.path = Path(path)
        self.fps = fps
        self.realtime = realtime
        self._seq = 0
        self._t0 = time.time()
        if self.path.is_dir():
            self._files = sorted(p for p in self.path.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
            self._cap = None
        else:
            self._files = None
            self._cap = cv2.VideoCapture(str(self.path))
            self.fps = self._cap.get(cv2.CAP_PROP_FPS) or fps

    def start(self) -> "FileSource":
        return self

    def read(self, last_seq: int = -1, wait_s: float = 0.0):
        if self._files is not None:
            if self._seq >= len(self._files):
                return last_seq, 0.0, None
            img = cv2.imread(str(self._files[self._seq]))
        else:
            ok, img = self._cap.read()
            if not ok:
                return last_seq, 0.0, None
        t = self._t0 + self._seq / self.fps
        if self.realtime:
            delay = t - time.time()
            if delay > 0:
                time.sleep(delay)
        self._seq += 1
        return self._seq, t, img

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
