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


SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def split_jpegs(buf: bytearray, max_buf: int = 4_000_000) -> list[bytes]:
    """Remove and return every complete JPEG in `buf`, in stream order.

    The multipart headers between frames are discarded; an incomplete JPEG at the end
    stays in the buffer for the next chunk. ESP32 JPEGs carry no EXIF thumbnail, so the
    first EOI after an SOI ends the image (0xFF inside entropy data is stuffed as FF00).
    """
    out: list[bytes] = []
    while True:
        start = buf.find(SOI)
        if start < 0:
            del buf[:-1]  # keep a trailing 0xFF that may begin the next SOI
            return out
        end = buf.find(EOI, start + 2)
        if end < 0:
            del buf[:start]
            if len(buf) > max_buf:  # garbage without an end marker
                buf.clear()
            return out
        out.append(bytes(buf[start:end + 2]))
        del buf[:end + 2]


def iter_mjpeg(url: str, timeout: float = 5.0, stop: threading.Event | None = None):
    """Yield (arrival_time, jpeg_bytes) for every frame of an MJPEG HTTP stream."""
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(chunk_size=16384):
            if stop is not None and stop.is_set():
                return
            buf += chunk
            for jpg in split_jpegs(buf):
                yield time.time(), jpg


class MjpegStream:
    def __init__(self, url: str, timeout: float = 5.0, reconnect_s: float = 2.0, on_jpeg=None):
        self.url = url
        self.timeout = timeout
        self.reconnect_s = reconnect_s
        self.on_jpeg = on_jpeg  # called with (t, jpeg_bytes) for every frame, e.g. Recorder.add
        self.connected = False
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
                log.info("stream connecting: %s", self.url)
                for t, jpg in iter_mjpeg(self.url, self.timeout, self._stop):
                    self.received += 1
                    self.connected = True
                    if self.on_jpeg is not None:
                        self.on_jpeg(t, jpg)
                    # Decoding every frame is cheap (~2 ms); only the newest one is kept.
                    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        with self._lock:
                            self._frame, self._t = img, t
                            self._seq += 1
                if self._stop.is_set():
                    return
            except Exception as e:
                self.connected = False
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


def imread_any(path: Path) -> np.ndarray | None:
    """cv2.imread cannot open non-ASCII paths on Windows (e.g. a Korean user folder)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def load_timestamps(folder: Path) -> list[tuple[str, float]] | None:
    """(file name, seconds from start) rows of a recording made by tools/record.py."""
    csv_path = folder / "timestamps.csv"
    if not csv_path.exists():
        return None
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        i_name, i_rel = header.index("file"), header.index("t_rel")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) > max(i_name, i_rel):
                rows.append((parts[i_name], float(parts[i_rel])))
    return rows


class FileSource:
    """Replays a recording folder, an image folder or a video file.

    Recording folders (tools/record.py) are replayed with their real frame times, so
    blink/TTC/smoothing behave as they did live. Default: every frame is processed in
    order (repeatable, for tuning). realtime=True: frames that are already late are
    skipped, like the live stream does when the detector is slow.
    """

    def __init__(self, path: str, fps: float = 10.0, realtime: bool = False):
        self.path = Path(path)
        self.fps = fps
        self.realtime = realtime
        self._seq = 0
        self._t0 = time.time()
        self._cap = None
        self._times: list[float] | None = None
        if self.path.is_dir():
            stamps = load_timestamps(self.path)
            if stamps:
                self._files = [self.path / name for name, _ in stamps]
                self._times = [t for _, t in stamps]
            else:
                self._files = sorted(p for p in self.path.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        else:
            self._files = None
            self._cap = cv2.VideoCapture(str(self.path))
            self.fps = self._cap.get(cv2.CAP_PROP_FPS) or fps

    def start(self) -> "FileSource":
        self._t0 = time.time()
        return self

    def _rel(self, i: int) -> float:
        return self._times[i] if self._times is not None else i / self.fps

    def __len__(self) -> int:
        return len(self._files) if self._files is not None else int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

    def read(self, last_seq: int = -1, wait_s: float = 0.0):
        if self.realtime and self._files is not None:
            # Jump to the newest frame whose time has come; wait if we are early.
            elapsed = time.time() - self._t0
            i = self._seq
            while i + 1 < len(self._files) and self._rel(i + 1) <= elapsed:
                i += 1
            if i < len(self._files) and self._rel(i) > elapsed:
                time.sleep(self._rel(i) - elapsed)
            self._seq = i
        if self._files is not None:
            img = None
            while img is None:  # skip unreadable (e.g. truncated) files
                if self._seq >= len(self._files):
                    return last_seq, 0.0, None
                img = imread_any(self._files[self._seq])
                if img is None:
                    log.warning("skipping unreadable frame %s", self._files[self._seq].name)
                    self._seq += 1
        else:
            ok, img = self._cap.read()
            if not ok:
                return last_seq, 0.0, None
        t = self._t0 + self._rel(self._seq)
        if self.realtime and self._files is None:
            delay = t - time.time()
            if delay > 0:
                time.sleep(delay)
        self._seq += 1
        return self._seq, t, img

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
