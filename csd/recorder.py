"""Session recording: raw camera JPEGs + arrival times (+ voice labels) in one folder.

Layout written here and read back by `stream.FileSource` and `tools/build_tl_dataset.py`:
  recordings/<YYYYmmdd_HHMMSS>[_name]/
    000000.jpg ...            frames exactly as the camera sent them
    timestamps.csv            index,file,t_unix,t_rel,bytes
    labels.csv                t_unix,label,heard,track_id,x1,y1,x2,y2   (voice labels)
    crops/<label>/*.jpg       traffic-light crops saved at label time
    meta.json
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class Recorder:
    def __init__(self, folder: Path, max_mb: float = 4000):
        self.folder = Path(folder)
        self.max_bytes = max_mb * 1e6
        self.folder.mkdir(parents=True, exist_ok=False)
        self._csv = open(self.folder / "timestamps.csv", "w", encoding="utf-8", newline="\n")
        self._csv.write("index,file,t_unix,t_rel,bytes\n")
        self._lock = threading.Lock()
        self.frames = 0
        self.bytes = 0
        self.t_first: float | None = None
        self.t_last = 0.0
        self.full = False
        self.meta: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "reconnects": 0, "gaps": []}

    @classmethod
    def new_session(cls, parent: Path, name: str = "", max_mb: float = 4000) -> "Recorder":
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return cls(Path(parent) / (f"{stamp}_{name}" if name else stamp), max_mb)

    def add(self, t: float, jpg: bytes) -> bool:
        """Save one frame; returns False (and stops saving) once the size limit is reached."""
        if self.full:
            return False
        with self._lock:
            if self.t_first is None:
                self.t_first = t
            name = f"{self.frames:06d}.jpg"
            with open(self.folder / name, "wb") as f:
                f.write(jpg)
            self._csv.write(f"{self.frames},{name},{t:.3f},{t - self.t_first:.3f},{len(jpg)}\n")
            self.frames += 1
            self.bytes += len(jpg)
            self.t_last = t
            if self.frames % 20 == 0:
                self._csv.flush()
            if self.bytes >= self.max_bytes:
                self.full = True
        return not self.full

    @property
    def duration(self) -> float:
        return self.t_last - self.t_first if self.t_first else 0.0

    def close(self, **extra) -> dict:
        with self._lock:
            self._csv.close()
        self.meta.update(extra)
        self.meta.update({
            "ended": time.strftime("%Y-%m-%dT%H:%M:%S"), "frames": self.frames,
            "duration_s": round(self.duration, 3), "bytes": self.bytes,
            "avg_fps": round(self.frames / self.duration, 2) if self.duration > 0 else 0.0,
        })
        (self.folder / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.meta
