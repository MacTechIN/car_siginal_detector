"""Is our own car moving? Estimated from background optical flow (no CAN/GPS).

Sparse Lucas-Kanade flow is tracked on a small grayscale image with vehicle/person boxes
masked out. The median flow magnitude, normalised by frame interval, stays near zero
when the car is stopped.
"""

from __future__ import annotations

import collections

import cv2
import numpy as np


class EgoMotion:
    def __init__(self, width: int = 320, still_px_per_s: float = 4.0, window_s: float = 1.0):
        self.width = width
        self.still_px_per_s = still_px_per_s
        self.window_s = window_s
        self._prev: np.ndarray | None = None
        self._prev_t = 0.0
        self._hist: collections.deque[tuple[float, float]] = collections.deque()

    def update(self, bgr: np.ndarray, t: float, boxes_xyxy: list | None = None) -> str:
        """Returns 'stopped', 'moving' or 'unknown'."""
        H, W = bgr.shape[:2]
        s = self.width / W
        gray = cv2.cvtColor(cv2.resize(bgr, (self.width, int(H * s))), cv2.COLOR_BGR2GRAY)
        state = "unknown"
        if self._prev is not None and t > self._prev_t:
            mask = np.full(gray.shape, 255, np.uint8)
            for x1, y1, x2, y2 in boxes_xyxy or []:
                cv2.rectangle(mask, (int(x1 * s), int(y1 * s)), (int(x2 * s), int(y2 * s)), 0, -1)
            pts = cv2.goodFeaturesToTrack(self._prev, maxCorners=150, qualityLevel=0.01, minDistance=7, mask=mask)
            if pts is not None and len(pts) >= 10:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(self._prev, gray, pts, None, winSize=(15, 15), maxLevel=2)
                ok = st.ravel() == 1
                if ok.sum() >= 8:
                    mag = np.linalg.norm((nxt[ok] - pts[ok]).reshape(-1, 2), axis=1)
                    speed = float(np.median(mag)) / (t - self._prev_t)  # px/s at `width`
                    self._hist.append((t, speed))
        self._prev, self._prev_t = gray, t
        while self._hist and t - self._hist[0][0] > self.window_s:
            self._hist.popleft()
        if len(self._hist) >= 3:
            med = float(np.median([v for _, v in self._hist]))
            state = "stopped" if med < self.still_px_per_s else "moving"
        return state
