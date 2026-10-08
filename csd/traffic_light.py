"""Korean traffic light state from a detector crop.

Korean vehicle signals are mostly horizontal heads, read left to right:
  3-lamp: red | yellow | green
  4-lamp: red | yellow | left-arrow | green
Vertical heads (and pedestrian signals) are read top to bottom: red ... green.

The crop is split into equal lamp cells along the long axis. A cell counts as lit when
its bright, saturated pixels exceed a share of the cell; the hue of those pixels is used
as a cross-check against the cell position. Flashing (yellow or red blinking at ~1 Hz)
is detected over time per track by `FlashTracker`.

The detector often boxes only the lit lamp of a far or night-time head (a near-square
crop). Splitting that into lamp cells puts the over-exposed centre in the middle cell and
reads a red lamp as yellow (2026-10-06 road recordings), so near-square crops are read by
hue alone. Lamp combinations a Korean vehicle signal never shows are reported as unknown.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

import cv2
import numpy as np

# OpenCV hue is 0..179
_HUE_RANGES = {
    "red": [(0, 12), (160, 179)],
    "yellow": [(13, 40)],
    "green": [(41, 100)],
}


@dataclass
class LightReading:
    state: str            # red, yellow, green, left, red_left, green_left, off, unknown
    lamps: int            # 3 or 4, 1 for a single-lamp crop (0 when unknown)
    orientation: str      # horizontal / vertical
    lit: tuple[str, ...]  # lit lamp roles in order
    confidence: float


def _hue_class(h: np.ndarray) -> str | None:
    if h.size == 0:
        return None
    counts = {}
    for name, ranges in _HUE_RANGES.items():
        counts[name] = sum(int(((h >= lo) & (h <= hi)).sum()) for lo, hi in ranges)
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else None


def _lamp_roles(n: int) -> list[str]:
    return ["red", "yellow", "left", "green"] if n == 4 else ["red", "yellow", "green"]


def _estimate_lamp_count(long_px: int, short_px: int) -> int:
    ratio = long_px / max(short_px, 1)
    return 4 if ratio >= 3.4 else 3


# A head has 3-4 lamps in a row (long/short side ratio >= ~2.4 in practice); below this the
# crop is taken to be one lamp.
SINGLE_LAMP_RATIO = 2.0
# Korean vehicle signals: one lamp, or the arrow together with red or green.
VALID_STATES = {"off", "red", "yellow", "green", "left", "red_left", "green_left"}


def _single_lamp(hsv: np.ndarray, orientation: str, s_min: int, min_colored: float = 0.03,
                 min_purity: float = 0.7) -> LightReading:
    """One lit lamp: its colour from the saturated halo around the (often white) centre."""
    colored = (hsv[..., 1] >= s_min) & (hsv[..., 2] >= 110)
    share = float(colored.mean())
    if share < min_colored:
        return LightReading("unknown", 1, orientation, (), 0.0)
    h = hsv[..., 0][colored]
    counts = {name: sum(int(((h >= lo) & (h <= hi)).sum()) for lo, hi in ranges)
              for name, ranges in _HUE_RANGES.items()}
    best = max(counts, key=counts.get)
    purity = counts[best] / max(1, h.size)
    if purity < min_purity:
        return LightReading("unknown", 1, orientation, (), 0.0)
    return LightReading(best, 1, orientation, (best,), min(1.0, share / 0.1) * purity)


def classify_light(crop: np.ndarray, lit_share: float = 0.08, v_min: int = 170, s_min: int = 70) -> LightReading:
    """Classify a BGR crop of one traffic-light head (or of one lit lamp)."""
    if crop is None or crop.size == 0 or min(crop.shape[:2]) < 4:
        return LightReading("unknown", 0, "horizontal", (), 0.0)

    h, w = crop.shape[:2]
    horizontal = w >= h
    orientation = "horizontal" if horizontal else "vertical"
    long_px, short_px = (w, h) if horizontal else (h, w)
    if long_px / max(short_px, 1) < SINGLE_LAMP_RATIO:
        return _single_lamp(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), orientation, s_min)
    n = _estimate_lamp_count(long_px, short_px)
    roles = _lamp_roles(n)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # A lamp is "lit" if it has bright pixels; saturated pixels give its colour.
    bright = (hsv[..., 2] >= v_min)
    colored = bright & (hsv[..., 1] >= s_min)

    lit_roles: list[str] = []
    scores: list[float] = []
    for i, role in enumerate(roles):
        a, b = int(i * long_px / n), int((i + 1) * long_px / n)
        sl = (slice(None), slice(a, b)) if horizontal else (slice(a, b), slice(None))
        cell_bright = bright[sl]
        share = float(cell_bright.mean()) if cell_bright.size else 0.0
        if share < lit_share:
            continue
        hue = _hue_class(hsv[..., 0][sl][colored[sl]])
        # Position decides the role; hue only vetoes clear contradictions
        # (e.g. a bright white reflection in the red cell).
        expected = {"red": "red", "yellow": "yellow", "left": "green", "green": "green"}[role]
        if hue is not None and hue != expected and not (expected == "yellow" and hue == "red"):
            continue
        lit_roles.append(role)
        scores.append(min(1.0, share / (lit_share * 3)))

    state = _combine(lit_roles)
    conf = float(np.mean(scores)) if scores else 0.5
    if state == "off":
        # No lamp is bright enough: fall back to the dominant hue of the whole head so a
        # dim or over-exposed light still yields a colour (lower confidence).
        hue = _hue_class(hsv[..., 0][(hsv[..., 2] >= int(v_min * 0.75)) & (hsv[..., 1] >= s_min)])
        if hue is not None:
            state, conf = hue, 0.3
    if state not in VALID_STATES:
        return LightReading("unknown", n, orientation, tuple(lit_roles), 0.0)
    return LightReading(state, n, orientation, tuple(lit_roles), conf)


def _combine(lit: list[str]) -> str:
    s = set(lit)
    if not s:
        return "off"
    if s == {"red"}:
        return "red"
    if s == {"yellow"}:
        return "yellow"
    if s == {"green"}:
        return "green"
    if s == {"left"}:
        return "left"
    if s == {"red", "left"}:
        return "red_left"
    if s == {"green", "left"}:
        return "green_left"
    if s == {"red", "yellow"}:
        return "red_yellow"
    return "+".join(sorted(s))


def lane_control_row(boxes: dict[int, tuple], min_count: int = 3) -> set[int]:
    """Track ids of square lamps standing in one row at the same height and size.

    Highway and tunnel lane-control signals (green down-arrow / red X, one per lane on a
    gantry) are detected as "traffic light" and were announced as green lights. Vehicle
    signal heads are wide (3-4 lamps), so a row of >= 3 near-square, equal-size lamps is
    taken to be lane-control signals.
    """
    sq = {tid: b for tid, b in boxes.items()
          if (b[2] - b[0]) > 0 and (b[3] - b[1]) > 0
          and max(b[2] - b[0], b[3] - b[1]) / min(b[2] - b[0], b[3] - b[1]) < SINGLE_LAMP_RATIO}
    best: set[int] = set()
    for tid, b in sq.items():
        h = b[3] - b[1]
        cy = (b[1] + b[3]) / 2
        row = {o for o, c in sq.items()
               if abs((c[1] + c[3]) / 2 - cy) <= 0.6 * h and 0.6 <= (c[3] - c[1]) / h <= 1.6}
        if len(row) > len(best):
            best = row
    return best if len(best) >= min_count else set()


class FlashTracker:
    """Detects a blinking lamp (flashing yellow / red) from on/off transitions of one track."""

    def __init__(self, window_s: float = 3.0, min_toggles: int = 3):
        self.window_s = window_s
        self.min_toggles = min_toggles
        self._hist: collections.deque[tuple[float, str]] = collections.deque()

    def update(self, state: str, t: float) -> str:
        self._hist.append((t, state))
        while self._hist and t - self._hist[0][0] > self.window_s:
            self._hist.popleft()
        seq = [s for _, s in self._hist]
        for color in ("yellow", "red"):
            onoff = [s == color for s in seq if s in (color, "off")]
            toggles = sum(1 for a, b in zip(onoff, onoff[1:]) if a != b)
            if toggles >= self.min_toggles and any(onoff) and not all(onoff):
                return f"flashing_{color}"
        return state
