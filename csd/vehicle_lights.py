"""Brake light and turn signal state of a vehicle seen from behind.

Per track, two lamp regions (rear-left and rear-right of the box) are measured every frame:
  - red_score:   share of bright, saturated red pixels  -> brake lights (steady)
  - amber_score: share of bright amber/orange pixels     -> turn signals (blink ~1-2 Hz)

Brake: the red score is compared with the track's own recent baseline (lamps off),
because tail lights are also red when not braking.
Turn: the amber score of each side is binarised against its running range and the
on/off toggles are counted over a time window; both sides blinking together = hazard.
Frame times come from the stream, so uneven Wi-Fi frame spacing is handled.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

import cv2
import numpy as np


def lamp_regions(box_xyxy, frame_shape) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Rear-left and rear-right lamp regions (x1, y1, x2, y2) inside a vehicle box."""
    x1, y1, x2, y2 = box_xyxy
    w, h = x2 - x1, y2 - y1
    ry1, ry2 = y1 + 0.30 * h, y1 + 0.75 * h
    left = (x1, ry1, x1 + 0.32 * w, ry2)
    right = (x2 - 0.32 * w, ry1, x2, ry2)
    H, W = frame_shape[:2]

    def clip(r):
        a, b, c, d = r
        return (int(max(0, a)), int(max(0, b)), int(min(W, c)), int(min(H, d)))

    return clip(left), clip(right)


def color_scores(bgr: np.ndarray) -> tuple[float, float]:
    """(red_score, amber_score) = share of bright red / amber pixels in a region."""
    if bgr is None or bgr.size == 0:
        return 0.0, 0.0
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bright = (v >= 150) & (s >= 90)
    red = bright & ((h <= 8) | (h >= 165))
    amber = bright & (h >= 9) & (h <= 30)
    n = float(h.size)
    return float(red.sum()) / n, float(amber.sum()) / n


@dataclass
class LightState:
    brake: bool
    turn: str  # none, left_turn, right_turn, hazard

    def label(self) -> str:
        parts = ["brake_on" if self.brake else "brake_off"]
        if self.turn != "none":
            parts.append(self.turn)
        return ",".join(parts)


class _Blink:
    def __init__(self, window_s: float):
        self.window_s = window_s
        self.samples: collections.deque[tuple[float, float]] = collections.deque()

    def update(self, t: float, value: float) -> None:
        self.samples.append((t, value))
        while self.samples and t - self.samples[0][0] > self.window_s:
            self.samples.popleft()

    def blinking(self, min_range: float, min_toggles: int, fmin: float = 0.6, fmax: float = 3.0) -> bool:
        if len(self.samples) < 6:
            return False
        ts = np.array([s[0] for s in self.samples])
        vs = np.array([s[1] for s in self.samples])
        lo, hi = float(np.percentile(vs, 10)), float(np.percentile(vs, 90))
        if hi - lo < min_range:
            return False
        on = vs > (lo + hi) / 2
        toggles = int(np.count_nonzero(on[1:] != on[:-1]))
        span = ts[-1] - ts[0]
        if span <= 0 or toggles < min_toggles:
            return False
        freq = toggles / 2.0 / span  # full on/off cycles per second
        return fmin <= freq <= fmax


class VehicleLightTracker:
    """Keeps lamp history for one vehicle track."""

    def __init__(self, window_s: float = 2.5, brake_ratio: float = 1.8, brake_abs: float = 0.04):
        self.left = _Blink(window_s)
        self.right = _Blink(window_s)
        self.red_hist: collections.deque[float] = collections.deque(maxlen=60)
        self.brake_ratio = brake_ratio
        self.brake_abs = brake_abs

    def update(self, frame: np.ndarray, box_xyxy, t: float) -> LightState:
        lr, rr = lamp_regions(box_xyxy, frame.shape)
        l_red, l_amb = color_scores(frame[lr[1]:lr[3], lr[0]:lr[2]])
        r_red, r_amb = color_scores(frame[rr[1]:rr[3], rr[0]:rr[2]])

        red = (l_red + r_red) / 2
        # Baseline = lower quartile of recent non-braking frames, so a long stop at a red
        # light does not slowly turn the brake state off.
        base = float(np.percentile(self.red_hist, 25)) if len(self.red_hist) >= 5 else None
        brake = base is not None and red >= self.brake_abs and red >= base * self.brake_ratio + 0.01
        if not brake:
            self.red_hist.append(red)

        # Use red+amber for blinking: many Korean cars use red rear turn lamps.
        self.left.update(t, l_amb + 0.5 * l_red)
        self.right.update(t, r_amb + 0.5 * r_red)
        lb = self.left.blinking(min_range=0.02, min_toggles=3)
        rb = self.right.blinking(min_range=0.02, min_toggles=3)
        turn = "hazard" if lb and rb else "left_turn" if lb else "right_turn" if rb else "none"
        if turn == "hazard":
            brake = False  # both sides blinking red is a hazard, not braking
        return LightState(brake, turn)
