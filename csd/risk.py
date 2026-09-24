"""Lead vehicle, time-to-collision, lead motion state and cut-in detection.

TTC uses the scale-change method from Dagan et al., "Forward collision warning with a
single camera" (IEEE IV 2004): for a box width w(t), TTC = 1 / (d ln w / dt). The slope is
a least-squares fit over a short window using real frame times, so it tolerates uneven
Wi-Fi frame spacing. No distance or camera calibration is needed.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

import numpy as np

from .lanes import polygon_x_range

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle"}


@dataclass
class Kinematics:
    samples: collections.deque = field(default_factory=lambda: collections.deque(maxlen=90))
    offsets: collections.deque = field(default_factory=lambda: collections.deque(maxlen=90))
    receding_since: float | None = None
    last_cut_in: float = -1e9

    def add(self, t: float, box, offset: float) -> None:
        x1, y1, x2, y2 = box
        self.samples.append((t, max(x2 - x1, 1.0), (x1 + x2) / 2, y2))
        self.offsets.append((t, offset))

    def scale_rate(self, window_s: float = 0.8, min_n: int = 4) -> float | None:
        """d ln(width) / dt over the recent window (1/s); positive = getting closer."""
        if not self.samples:
            return None
        t_end = self.samples[-1][0]
        pts = [(t, w) for t, w, _, _ in self.samples if t_end - t <= window_s]
        if len(pts) < min_n:
            return None
        ts = np.array([p[0] for p in pts]) - pts[0][0]
        if ts[-1] <= 0.15:
            return None
        k, _ = np.polyfit(ts, np.log([p[1] for p in pts]), 1)
        return float(k)

    def ttc(self, window_s: float = 0.8) -> float | None:
        k = self.scale_rate(window_s)
        if k is None or k <= 0.02:
            return None
        return 1.0 / k


def lane_offset(box, polygon, frame_h: int) -> float:
    """Box bottom-centre offset from the ego-lane centre in half-lane widths (0 = centred,
    +-1 = on the lane line, negative = left)."""
    x1, y1, x2, y2 = box
    y = min(float(y2), frame_h - 1.0)
    xl, xr = polygon_x_range(polygon, y)
    half = max((xr - xl) / 2, 1.0)
    return ((x1 + x2) / 2 - (xl + xr) / 2) / half


class RiskAnalyzer:
    def __init__(self, ttc_warn: float = 2.7, ttc_danger: float = 2.0, min_box_w_ratio: float = 0.06,
                 lead_offset: float = 0.8, cut_in_outside: float = 1.15, cut_in_inside: float = 0.75,
                 cut_in_outside_s: float = 0.5, cut_in_cooldown_s: float = 6.0):
        self.ttc_warn = ttc_warn
        self.ttc_danger = ttc_danger
        self.min_box_w_ratio = min_box_w_ratio
        self.lead_offset = lead_offset
        self.cut_in_outside = cut_in_outside
        self.cut_in_inside = cut_in_inside
        self.cut_in_outside_s = cut_in_outside_s
        self.cut_in_cooldown_s = cut_in_cooldown_s
        self.kin: dict[int, Kinematics] = {}

    def update_track(self, tid: int, box, t: float, polygon, frame_h: int) -> Kinematics:
        k = self.kin.setdefault(tid, Kinematics())
        k.add(t, box, lane_offset(box, polygon, frame_h))
        return k

    def drop_missing(self, alive: set[int]) -> None:
        for tid in [t for t in self.kin if t not in alive]:
            del self.kin[tid]

    def select_lead(self, vehicles: dict[int, tuple], polygon, frame_h: int) -> int | None:
        """Closest vehicle (largest box bottom) whose bottom-centre is inside the ego lane."""
        best, best_y = None, -1.0
        for tid, box in vehicles.items():
            if abs(lane_offset(box, polygon, frame_h)) <= self.lead_offset and box[3] > best_y:
                best, best_y = tid, box[3]
        return best

    def collision_level(self, tid: int, box, frame_w: int) -> tuple[str, float | None]:
        """'none' / 'warning' / 'danger' plus the TTC in seconds."""
        if (box[2] - box[0]) < frame_w * self.min_box_w_ratio:
            return "none", None  # too far/small for a stable scale estimate
        ttc = self.kin[tid].ttc()
        if ttc is None:
            return "none", None
        if ttc < self.ttc_danger:
            return "danger", ttc
        if ttc < self.ttc_warn:
            return "warning", ttc
        return "none", ttc

    def lead_state(self, tid: int, ego: str, t: float) -> str | None:
        """stopped / starting / moving / slowing for the lead vehicle.

        Only relative motion is observable. With our car stopped, a receding lead is
        starting (first 2 s) then moving, and a constant-size lead is stopped. With our
        car moving, a constant or receding lead is moving and a fast-closing one is slowing.
        """
        kin = self.kin[tid]
        k = kin.scale_rate(window_s=1.0, min_n=5)
        if k is None:
            return None
        if k < -0.04:
            kin.receding_since = kin.receding_since or t
        else:
            kin.receding_since = None
        if ego == "stopped":
            if kin.receding_since is not None:
                return "starting" if t - kin.receding_since < 2.0 else "moving"
            return "stopped" if abs(k) < 0.03 else None
        if ego == "moving":
            return "slowing" if k > 0.12 else "moving"
        return None

    def cut_in(self, tid: int, box, frame_w: int, t: float) -> str | None:
        """'cut_in_left' / 'cut_in_right' when a vehicle moves from beside us into our lane."""
        kin = self.kin[tid]
        if (box[2] - box[0]) < frame_w * self.min_box_w_ratio * 1.3 or t - kin.last_cut_in < self.cut_in_cooldown_s:
            return None
        offs = list(kin.offsets)
        if len(offs) < 5:
            return None
        now = offs[-1][1]
        recent = [o for _, o in offs[-4:]]
        approaching = all(abs(a) >= abs(b) - 0.02 for a, b in zip(recent, recent[1:]))
        if abs(now) > self.cut_in_inside or not approaching:
            return None
        # It must have been clearly outside our lane for a while, 0.5-3 s ago.
        outside = [(ts, o) for ts, o in offs if 0 < t - ts <= 3.0 and abs(o) >= self.cut_in_outside]
        if not outside or outside[-1][0] - outside[0][0] < self.cut_in_outside_s:
            return None
        kin.last_cut_in = t
        return "cut_in_left" if outside[-1][1] < 0 else "cut_in_right"
