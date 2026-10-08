"""Ego-lane estimation from a forward camera (classical, CPU-cheap).

White and yellow lane paint is isolated in HLS, edges are taken in a road ROI below the
horizon, and Hough segments are split by slope into left/right boundary candidates.
Each boundary is a line x = a*y + b, smoothed with an EMA. When a boundary is not seen
for a while, the configured default corridor is used instead, so downstream logic
(lead vehicle, cut-in, collision) always has an ego lane polygon.

With a learned model (lane_model.AsyncLaneModel, TwinLiteNet) its lines are preferred:
curved, with paint colour and solid/dashed, and the bonnet line it finds keeps the
classical search off the bonnet. The classical result fills in a side the model misses.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class LaneResult:
    left: tuple[float, float] | None    # (a, b) for x = a*y + b, image pixels
    right: tuple[float, float] | None
    polygon: np.ndarray                  # ego lane polygon (4x2 int32), always present
    detected: bool                       # both boundaries come from the image
    departure: str                       # none, left, right
    left_line: object = None             # lane_model.LaneLine (curve, colour) when from the model
    right_line: object = None
    hood_y: float | None = None          # first bonnet row, None if unknown
    source: str = "classic"              # model / mixed / classic / default


class LaneDetector:
    def __init__(self, horizon_ratio: float = 0.55, default_bottom_width: float = 0.9,
                 default_top_width: float = 0.12, ema: float = 0.3, stale_frames: int = 15,
                 departure_margin: float = 0.18, model=None, model_max_age_s: float = 3.0):
        self.horizon_ratio = horizon_ratio
        self.default_bottom_width = default_bottom_width
        self.default_top_width = default_top_width
        self.ema = ema
        self.stale_frames = stale_frames
        self.departure_margin = departure_margin
        self._left: tuple[float, float] | None = None
        self._right: tuple[float, float] | None = None
        self._left_age = self._right_age = 10**6
        self.model = model                   # lane_model.AsyncLaneModel or None
        self.model_max_age_s = model_max_age_s
        self.hood_y: float | None = None     # last bonnet line seen by the model
        self._model_t = None
        self._mleft = self._mright = None    # smoothed model lines

    def _model_lines(self, t: float):
        """Latest model lines (EMA over successive model results), or (None, None)."""
        r = self.model.result if self.model is not None else None
        if r is None or t - r.t > self.model_max_age_s:
            self._mleft = self._mright = None
            return None, None
        if r.t != self._model_t:
            self._model_t = r.t
            if r.hood_y is not None:
                self.hood_y = r.hood_y if self.hood_y is None else 0.8 * self.hood_y + 0.2 * r.hood_y
            self._mleft = _blend(self._mleft, r.left)
            self._mright = _blend(self._mright, r.right)
        return self._mleft, self._mright

    def _paint_mask(self, bgr: np.ndarray) -> np.ndarray:
        hls = cv2.cvtColor(bgr, cv2.COLOR_BGR2HLS)
        white = cv2.inRange(hls, (0, 190, 0), (179, 255, 255))
        yellow = cv2.inRange(hls, (12, 80, 90), (38, 220, 255))
        return cv2.bitwise_or(white, yellow)

    def _fit(self, segs: list[tuple[int, int, int, int]]) -> tuple[float, float] | None:
        if not segs:
            return None
        ys, xs, ws = [], [], []
        for x1, y1, x2, y2 in segs:
            length = float(np.hypot(x2 - x1, y2 - y1))
            ys += [y1, y2]
            xs += [x1, x2]
            ws += [length, length]
        a, b = np.polyfit(np.array(ys, float), np.array(xs, float), 1, w=np.array(ws))
        return float(a), float(b)

    def _default(self, W: int, H: int) -> tuple[tuple[float, float], tuple[float, float]]:
        yt, yb = H * self.horizon_ratio, float(H)
        cx = W / 2
        lt, lb = cx - W * self.default_top_width / 2, cx - W * self.default_bottom_width / 2
        rt, rb = cx + W * self.default_top_width / 2, cx + W * self.default_bottom_width / 2
        la = (lb - lt) / (yb - yt)
        ra = (rb - rt) / (yb - yt)
        return (la, lt - la * yt), (ra, rt - ra * yt)

    def update(self, bgr: np.ndarray, t: float | None = None) -> LaneResult:
        H, W = bgr.shape[:2]
        if self.model is not None:
            self.model.submit(bgr, t if t is not None else 0.0)
        m_left, m_right = self._model_lines(t) if (self.model is not None and t is not None) else (None, None)
        y0 = int(H * self.horizon_ratio)
        roi = bgr[y0:]
        mask = self._paint_mask(roi)
        if self.hood_y is not None:
            mask[max(0, int(self.hood_y) - y0 - 2):] = 0  # bonnet reflections are not paint
        edges = cv2.Canny(cv2.GaussianBlur(mask, (5, 5), 0), 50, 150)
        # The whole road area below the horizon is searched (a narrow ROI loses the far
        # boundary as soon as the car drifts toward one side).
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=25,
                                minLineLength=max(15, H // 20), maxLineGap=H // 12)

        left_cand, right_cand = [], []
        if lines is not None:
            # (N, 1, 4) in OpenCV 4, (N, 4) in OpenCV 5
            for x1, y1, x2, y2 in lines.reshape(-1, 4):
                if x2 == x1:
                    continue
                slope = (y2 - y1) / (x2 - x1)
                if abs(slope) < 0.4:
                    continue  # nearly horizontal: stop lines, shadows
                seg = (int(x1), int(y1 + y0), int(x2), int(y2 + y0))
                # Where this segment's line meets the bottom of the image
                x_bottom = seg[0] + (H - seg[1]) / slope
                if slope < 0 and x_bottom < W * 0.6:
                    left_cand.append((x_bottom, seg))
                elif slope > 0 and x_bottom > W * 0.4:
                    right_cand.append((x_bottom, seg))
        # Our lane's boundaries are the lines closest to the image centre at the bottom;
        # lines further out belong to neighbouring lanes.
        left_segs = self._nearest(left_cand, W, pick_max=True)
        right_segs = self._nearest(right_cand, W, pick_max=False)

        self._left, self._left_age = self._smooth(self._left, self._fit(left_segs), self._left_age)
        self._right, self._right_age = self._smooth(self._right, self._fit(right_segs), self._right_age)

        d_left, d_right = self._default(W, H)
        left = self._left if self._left_age <= self.stale_frames else None
        right = self._right if self._right_age <= self.stale_frames else None
        if m_left is not None:
            left = m_left.linear()
        if m_right is not None:
            right = m_right.linear()
        detected = left is not None and right is not None
        # Sanity: the lanes must not cross above the bottom of the image.
        if detected and (left[0] * H + left[1]) >= (right[0] * H + right[1]):
            left = right = None
            detected = False
        use_l, use_r = left or d_left, right or d_right

        yt, yb = y0, H
        polygon = np.array([
            [use_l[0] * yb + use_l[1], yb], [use_l[0] * yt + use_l[1], yt],
            [use_r[0] * yt + use_r[1], yt], [use_r[0] * yb + use_r[1], yb],
        ], dtype=np.int32)

        departure = "none"
        if detected:
            yc = self.hood_y or H  # where the car is: the bonnet line
            xl, xr = use_l[0] * yc + use_l[1], use_r[0] * yc + use_r[1]
            lane_w = xr - xl
            car_x = W / 2
            if lane_w > W * 0.25:
                if car_x - xl < lane_w * self.departure_margin:
                    departure = "left"
                elif xr - car_x < lane_w * self.departure_margin:
                    departure = "right"
        n_model = (m_left is not None) + (m_right is not None)
        source = ("model" if n_model == 2 else "mixed" if n_model else "classic") if detected else "default"
        return LaneResult(left, right, polygon, detected, departure,
                          m_left if detected else None, m_right if detected else None, self.hood_y, source)

    @staticmethod
    def _nearest(cands, W: int, pick_max: bool, band: float = 0.06):
        if not cands:
            return []
        ref = max(c[0] for c in cands) if pick_max else min(c[0] for c in cands)
        return [seg for xb, seg in cands if abs(xb - ref) <= W * band]

    def _smooth(self, prev, new, age):
        if new is None:
            return prev, age + 1
        if prev is None or age > self.stale_frames:
            return new, 0
        k = self.ema
        return (prev[0] * (1 - k) + new[0] * k, prev[1] * (1 - k) + new[1] * k), 0


def _blend(prev, new, k: float = 0.5):
    """EMA of lane-line curves between model results (colour/dashed from the newest)."""
    if new is None or prev is None:
        return new
    import copy
    out = copy.copy(new)
    out.coef = prev.coef * (1 - k) + new.coef * k
    return out


def lane_x_at(line: tuple[float, float], y: float) -> float:
    return line[0] * y + line[1]


def polygon_x_range(polygon: np.ndarray, y: float) -> tuple[float, float]:
    """Left/right x of the ego lane polygon at image row y (linear interpolation)."""
    (lbx, lby), (ltx, lty), (rtx, rty), (rbx, rby) = polygon.astype(float)
    y = min(max(y, lty), lby)
    t = (y - lty) / max(lby - lty, 1e-6)
    return ltx + (lbx - ltx) * t, rtx + (rbx - rtx) * t
