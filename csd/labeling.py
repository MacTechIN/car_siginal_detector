"""Turns voice labels into training crops inside a session folder.

The pipeline feeds every processed frame with the traffic light it considers relevant.
When a voice label arrives, crops of that light from the frames around the moment the
driver started speaking are saved to <session>/crops/<label>/ and appended to
<session>/labels.csv. Crops are letterboxed to a square so classifier resizing never
cuts off a lamp (a 4-lamp head is ~4:1).
"""

from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .voice_label import VoiceLabel

log = logging.getLogger(__name__)

LABEL_KO = {"red": "빨간불", "yellow": "노란불", "green": "초록불", "left": "좌회전", "green_left": "직진 좌회전",
            "red_left": "빨간불 좌회전", "red_yellow": "빨간불 노란불", "flashing_yellow": "황색 점멸",
            "flashing_red": "적색 점멸", "off": "신호 없음"}
# Flashing is a temporal state (decided by FlashTracker). Crops around a flashing label
# mix lit and dark frames, so they would poison the per-frame classes: log the event only.
NO_CROPS = {"flashing_yellow", "flashing_red"}


def square_crop(frame: np.ndarray, box, pad: float = 0.15, size: int = 96) -> np.ndarray | None:
    """Padded crop of `box`, letterboxed onto a square canvas of `size` px."""
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box
    pw, ph = (x2 - x1) * pad, (y2 - y1) * pad
    x1, y1 = int(max(0, x1 - pw)), int(max(0, y1 - ph))
    x2, y2 = int(min(W, x2 + pw)), int(min(H, y2 + ph))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    s = size / max(crop.shape[:2])
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))),
                      interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    canvas = np.zeros((size, size, 3), np.uint8)
    oy, ox = (size - crop.shape[0]) // 2, (size - crop.shape[1]) // 2
    canvas[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    return canvas


def imwrite_any(path: Path, img: np.ndarray) -> bool:
    """cv2.imwrite cannot write non-ASCII paths on Windows."""
    ok, buf = cv2.imencode(path.suffix or ".jpg", img)
    if ok:
        buf.tofile(str(path))
    return ok


@dataclass
class _Frame:
    t: float
    frame: np.ndarray
    box: tuple | None
    tid: int | None
    pred: str | None


@dataclass
class _Saved:
    label: str
    files: list[Path] = field(default_factory=list)


class LabelSession:
    def __init__(self, folder: str | Path, before_s: float = 1.0, after_s: float = 0.6, max_crops: int = 8):
        self.folder = Path(folder)
        self.before_s = before_s
        self.after_s = after_s
        self.max_crops = max_crops
        self._buf: collections.deque[_Frame] = collections.deque()
        self._pending: list[VoiceLabel] = []
        self._last: _Saved | None = None
        self.counts: collections.Counter[str] = collections.Counter()
        self.last_feedback: tuple[float, str] | None = None
        csv_path = self.folder / "labels.csv"
        new = not csv_path.exists()
        self.folder.mkdir(parents=True, exist_ok=True)
        self._csv = open(csv_path, "a", encoding="utf-8", newline="\n")
        if new:
            self._csv.write("t_unix,label,heard,track_id,x1,y1,x2,y2,crops\n")

    def add_frame(self, t: float, frame: np.ndarray, box, tid, pred: str | None) -> None:
        self._buf.append(_Frame(t, frame, tuple(box) if box is not None else None, tid, pred))
        while self._buf and t - self._buf[0].t > self.before_s + self.after_s + 2.0:
            self._buf.popleft()

    def submit(self, vl: VoiceLabel) -> None:
        self._pending.append(vl)

    def process(self, now: float) -> list[str]:
        """Handle labels whose time window has passed; returns voice feedback texts."""
        out = []
        for vl in list(self._pending):
            if vl.label not in ("cancel", "confirm") and now < vl.t + self.after_s:
                continue  # wait until frames after the utterance start have arrived
            self._pending.remove(vl)
            out.append(self._handle(vl))
        return [o for o in out if o]

    def _handle(self, vl: VoiceLabel) -> str:
        if vl.label == "cancel":
            if not self._last:
                return "취소할 라벨이 없습니다"
            for f in self._last.files:
                f.unlink(missing_ok=True)
            self.counts[self._last.label] -= len(self._last.files)
            self._row(vl.t, "cancel", vl.heard, None, None, 0)
            label, self._last = self._last.label, None
            return f"{LABEL_KO.get(label, label)} 라벨을 취소했습니다"

        label = vl.label
        if label == "confirm":
            latest = next((f for f in reversed(self._buf) if f.box is not None and f.pred), None)
            if latest is None:
                return "확인할 신호등이 없습니다"
            label = latest.pred

        frames = [f for f in self._buf if f.box is not None and vl.t - self.before_s <= f.t <= vl.t + self.after_s]
        if label in NO_CROPS:
            first = frames[0] if frames else None
            self._row(vl.t, label, vl.heard, first.tid if first else None, first.box if first else None, 0)
            self._last = None
            return f"{LABEL_KO.get(label, label)} 기록"
        if len(frames) > self.max_crops:
            step = len(frames) / self.max_crops
            frames = [frames[int(i * step)] for i in range(self.max_crops)]
        cls = label
        saved = _Saved(cls)
        d = self.folder / "crops" / cls
        d.mkdir(parents=True, exist_ok=True)
        event = int(vl.t * 1000)  # file name prefix = label event, used for train/val grouping
        for k, f in enumerate(frames):
            crop = square_crop(f.frame, f.box)
            if crop is None:
                continue
            p = d / f"{event}_{f.tid}_{k}.jpg"
            if imwrite_any(p, crop):
                saved.files.append(p)
        first = frames[0] if frames else None
        self._row(vl.t, label, vl.heard, first.tid if first else None, first.box if first else None, len(saved.files))
        self.counts[cls] += len(saved.files)
        self._last = saved
        name = LABEL_KO.get(label, label)
        if not saved.files:
            return f"{name}. 신호등이 보이지 않아 영상 시각만 기록했습니다"
        return f"{name} {len(saved.files)}장 저장"

    def _row(self, t, label, heard, tid, box, n) -> None:
        b = ",".join(f"{v:.0f}" for v in box) if box else ",,,"
        self._csv.write(f"{t:.3f},{label},{heard},{'' if tid is None else tid},{b},{n}\n")
        self._csv.flush()
        self.last_feedback = (time.time(), f"{label} ({heard})")

    def close(self) -> None:
        self._csv.close()
