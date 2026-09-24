"""Korean license plate reading for a vehicle crop.

Pipeline adapted from sauce-Git/korean-license-plate-detector (Apache-2.0), whose YOLO
weights are published at huggingface.co/sauce-hug/korean-license-plate-detector:
  plate_detect_v1   -> plate box inside the vehicle crop
  vertex_detect_v1  -> 4 plate corners for perspective correction
  syllable_detect_v1 -> one box per character (digits and Hangul syllables)
Characters are ordered by position (two-line plates: upper line first) and validated
against the Korean plate formats. `PlateVoter` requires the same text on several frames.
"""

from __future__ import annotations

import collections
import logging
import re
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

# Class index -> character, from the upstream kor_list.txt (same order as training).
KOR_LIST = list("0123456789가나다라마아자하거너더러머버서어저허고노도로모보소오조호구누두루무부수우주배외교임"
                "울산대구인천광전경기강원충북남전제세바사차카타파")
assert len(KOR_LIST) == 75

REGIONS = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원",
           "충북", "충남", "전북", "전남", "경북", "경남", "제주")
PLATE_RE = re.compile(r"^(?:[가-힣]{2})?\d{2,3}[가-힣]\d{4}$")


def is_valid_plate(text: str | None) -> bool:
    return bool(text) and PLATE_RE.match(text) is not None


def order_characters(boxes: list[tuple[float, float, float, float, str, float]]) -> str:
    """Order (x1, y1, x2, y2, char, conf) boxes into plate text.

    Boxes are clustered into lines by vertical centre; a line break exists when the
    vertical gap exceeds half the median character height.
    """
    if not boxes:
        return ""
    # Suppress overlapping duplicates, keeping the more confident one.
    boxes = sorted(boxes, key=lambda b: -b[5])
    kept: list[tuple] = []
    for b in boxes:
        if all(_iou(b, k) < 0.3 for k in kept):
            kept.append(b)
    heights = [b[3] - b[1] for b in kept]
    med_h = float(np.median(heights))
    kept.sort(key=lambda b: (b[1] + b[3]) / 2)
    lines: list[list[tuple]] = [[kept[0]]]
    for b in kept[1:]:
        prev_cy = np.mean([(p[1] + p[3]) / 2 for p in lines[-1]])
        if (b[1] + b[3]) / 2 - prev_cy > med_h * 0.5:
            lines.append([b])
        else:
            lines[-1].append(b)
    text = "".join(c[4] for line in lines for c in sorted(line, key=lambda b: b[0]))
    return text


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _warp_by_corners(img: np.ndarray, pts: np.ndarray) -> np.ndarray | None:
    if pts.shape[0] < 4:
        return None
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    tl, br, tr, bl = pts[np.argmin(s)], pts[np.argmax(s)], pts[np.argmin(d)], pts[np.argmax(d)]
    if tl[0] >= tr[0] or tl[1] >= bl[1] or br[0] <= bl[0] or br[1] <= tr[1]:
        return None
    w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if w < 8 or h < 4:
        return None
    m = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]), np.float32([[0, 0], [w, 0], [w, h], [0, h]]))
    return cv2.warpPerspective(img, m, (w, h))


class PlateReader:
    def __init__(self, model_dir: str | Path, conf: float = 0.35):
        from ultralytics import YOLO

        d = Path(model_dir)
        self.plate = YOLO(str(d / "plate_detect_v1.pt"), task="detect")
        self.vertex = YOLO(str(d / "vertex_detect_v1.pt"), task="detect")
        self.syllable = YOLO(str(d / "syllable_detect_v1.pt"), task="detect")
        self.conf = conf

    def _char(self, cls_idx: int) -> str:
        return KOR_LIST[cls_idx] if 0 <= cls_idx < len(KOR_LIST) else ""

    def _ocr(self, img: np.ndarray) -> str:
        r = self.syllable.predict(img, conf=self.conf, verbose=False)[0]
        boxes = [(*b.xyxy[0].tolist(), self._char(int(b.cls)), float(b.conf)) for b in r.boxes]
        return order_characters(boxes)

    def read(self, vehicle_crop: np.ndarray) -> tuple[str | None, np.ndarray | None]:
        """Return (plate text or None, plate box xyxy in crop coordinates or None)."""
        if vehicle_crop is None or vehicle_crop.size == 0:
            return None, None
        r = self.plate.predict(vehicle_crop, conf=self.conf, verbose=False)[0]
        if len(r.boxes) == 0:
            return None, None
        best = int(r.boxes.conf.argmax())
        x1, y1, x2, y2 = (int(v) for v in r.boxes.xyxy[best].tolist())
        pad = int(0.08 * (x2 - x1))
        H, W = vehicle_crop.shape[:2]
        x1, y1, x2, y2 = max(0, x1 - pad), max(0, y1 - pad), min(W, x2 + pad), min(H, y2 + pad)
        crop = vehicle_crop[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None
        # Small plates from a low-res camera: upscale before OCR.
        if crop.shape[1] < 160:
            f = 160 / crop.shape[1]
            crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)

        candidates = []
        v = self.vertex.predict(crop, conf=0.25, verbose=False)[0]
        if len(v.boxes) >= 4:
            pts = np.array([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] for b in v.boxes.xyxy.tolist()[:4]], dtype=np.float32)
            warped = _warp_by_corners(crop, pts)
            if warped is not None:
                candidates.append(warped)
        candidates.append(crop)
        for img in candidates:
            text = _normalise(self._ocr(img))
            if is_valid_plate(text):
                return text, np.array([x1, y1, x2, y2])
        return None, np.array([x1, y1, x2, y2])


def _normalise(text: str) -> str:
    """Fix a leading region prefix the way upstream does: the two region syllables of a
    two-line plate may come out swapped; anything else before the digits is dropped."""
    if len(text) > 2 and not text[0].isdigit():
        if text[:2] not in REGIONS and text[1] + text[0] in REGIONS:
            text = text[1] + text[0] + text[2:]
        if text[:2] not in REGIONS:
            text = text[2:]
    return text


class PlateVoter:
    """Confirms a plate for a track once the same text is read `min_votes` times."""

    def __init__(self, min_votes: int = 2, history: int = 6):
        self.reads: collections.deque[str] = collections.deque(maxlen=history)
        self.min_votes = min_votes
        self.confirmed: str | None = None

    def add(self, text: str | None) -> str | None:
        if not text:
            return None
        self.reads.append(text)
        best, n = collections.Counter(self.reads).most_common(1)[0]
        if n >= self.min_votes and best != self.confirmed:
            self.confirmed = best
            return best
        return None
