"""Learned lane lines: TwinLiteNet (BDD100K drivable area + lane-line segmentation).

TwinLiteNet (Che Quang Huy, MIT licence, 0.4M parameters) runs from ONNX through OpenVINO
on the laptop's Intel iGPU (about 10 ms at 320x192, leaving the CPU to the detector), with
the CPU as fallback. It runs on a background thread so the detector loop is never
blocked; the latest result is reused until it is `max_age_s` old.

From the masks this module takes:
  * the bonnet line: the lowest row where the drivable area still reaches the image
    centre (the bonnet is never "drivable"),
  * the ego lane's left/right lines: lane-line components fitted with x = f(y) (2nd
    order), nearest to the image centre at the bonnet line,
  * each line's paint colour (white / yellow / blue) from the camera pixels under it, and
    whether it is solid or dashed (mask coverage along the line).
Model export: tools/fetch_models.py --lanes (models/twinlitenet.onnx).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class LaneLine:
    coef: np.ndarray            # x = c0*y^2 + c1*y + c2 (full-frame pixels)
    y_top: float
    y_bottom: float
    color: str = "white"        # white / yellow / blue
    dashed: bool = False

    def x_at(self, y) -> np.ndarray:
        return np.polyval(self.coef, y)

    def linear(self) -> tuple[float, float]:
        """Straight-line (a, b), x = a*y + b, through the bottom part of the curve."""
        ys = np.linspace(self.y_bottom - (self.y_bottom - self.y_top) * 0.5, self.y_bottom, 8)
        a, b = np.polyfit(ys, self.x_at(ys), 1)
        return float(a), float(b)

    def points(self, n: int = 24) -> np.ndarray:
        ys = np.linspace(self.y_top, self.y_bottom, n)
        return np.stack([self.x_at(ys), ys], 1).astype(np.int32)

    @property
    def kind(self) -> str:
        return f"{self.color}_{'dashed' if self.dashed else 'solid'}"


@dataclass
class ModelLanes:
    t: float
    left: LaneLine | None
    right: LaneLine | None
    hood_y: float | None          # first bonnet row (full-frame pixels), None if unknown
    drivable: np.ndarray | None = field(default=None, repr=False)  # small mask (for debugging)


class TwinLiteNet:
    """OpenVINO on `device` ("GPU" = Intel iGPU, "CPU"); falls back to the CPU."""

    def __init__(self, onnx_path: str | Path, input_wh: tuple[int, int] = (320, 192), threads: int = 2,
                 device: str = "GPU", cache_dir: str | Path | None = None):
        import openvino as ov

        core = ov.Core()
        if cache_dir:
            core.set_property({"CACHE_DIR": str(cache_dir)})  # compiled GPU kernels: fast restarts
        model = core.read_model(str(onnx_path))
        w, h = input_wh
        model.reshape({"img": [1, 3, h, w]})
        self.input_wh = (w, h)
        self.device = device if device in core.available_devices else "CPU"
        try:
            cfg = {"INFERENCE_NUM_THREADS": threads} if self.device == "CPU" else {}
            self.compiled = core.compile_model(model, self.device, cfg)
        except Exception as e:
            log.warning("lane model on %s failed (%s); using the CPU", self.device, e)
            self.device = "CPU"
            self.compiled = core.compile_model(model, "CPU", {"INFERENCE_NUM_THREADS": threads})
        self.request = self.compiled.create_infer_request()

    def masks(self, bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(drivable, lane-line) uint8 masks at the model input size."""
        x = cv2.resize(bgr, self.input_wh, interpolation=cv2.INTER_AREA)[:, :, ::-1]
        x = x.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        res = self.request.infer({"img": x})
        da, ll = res[self.compiled.output(0)], res[self.compiled.output(1)]
        return da[0].argmax(0).astype(np.uint8), ll[0].argmax(0).astype(np.uint8)


# ---------------------------------------------------------------- mask -> lanes
def hood_row(drivable: np.ndarray, min_share: float = 0.15) -> int | None:
    """Lowest row (small-mask coordinates) where the drivable area still covers the
    centre strip; rows below it are the bonnet. None if the road is not seen."""
    h, w = drivable.shape
    centre = drivable[:, int(w * 0.35):int(w * 0.65)]
    share = centre.mean(axis=1)
    rows = np.nonzero(share >= min_share)[0]
    if rows.size == 0 or rows[-1] < h * 0.4:
        return None
    return int(rows[-1])


def road_gain(bgr_small: np.ndarray, drivable: np.ndarray, lanes: np.ndarray) -> np.ndarray | None:
    """Per-channel gains that make the asphalt neutral grey (removes the dusk-blue or
    tunnel-orange cast before the paint colour is judged). None if too little road."""
    road = (drivable > 0) & (lanes == 0)
    if road.sum() < 50:
        return None
    mean = bgr_small[road].reshape(-1, 3).mean(axis=0) + 1e-3
    return mean.mean() / mean


def paint_color(bgr: np.ndarray, pts: np.ndarray, radius: int = 2, gain: np.ndarray | None = None) -> str:
    """Dominant paint colour under a line (full-frame points)."""
    mask = np.zeros(bgr.shape[:2], np.uint8)
    cv2.polylines(mask, [pts], False, 255, max(1, radius * 2 + 1))
    px = bgr[mask > 0].astype(np.float32)
    if gain is not None:
        px = np.clip(px * gain, 0, 255)
    if px.size == 0:
        return "white"
    hsv = cv2.cvtColor(px.astype(np.uint8)[None], cv2.COLOR_BGR2HSV)[0]
    if hsv.size == 0:
        return "white"
    bright = hsv[hsv[:, 2] >= np.percentile(hsv[:, 2], 60)]  # the paint, not the asphalt beside it
    h, s = bright[:, 0].astype(int), bright[:, 1].astype(int)
    yellow = np.mean((h >= 12) & (h <= 35) & (s >= 70))
    blue = np.mean((h >= 95) & (h <= 125) & (s >= 110))  # blue (bus lane) paint is strongly saturated
    if yellow >= 0.35:
        return "yellow"
    if blue >= 0.5:
        return "blue"
    return "white"


def _peaks(hist: np.ndarray, min_val: float, min_gap: int) -> list[int]:
    """Column positions of local maxima, strongest first, at least `min_gap` apart."""
    order = np.argsort(hist)[::-1]
    out: list[int] = []
    for x in order:
        if hist[x] < min_val:
            break
        if all(abs(int(x) - o) >= min_gap for o in out):
            out.append(int(x))
    return out


def _track(ll: np.ndarray, x0: float, y_top: int, y_ref: int, n_win: int = 12):
    """Sliding windows from the bonnet line upward, following one lane line across dash
    gaps. Returns (row array, centre-x array, share of windows with paint)."""
    h, w = ll.shape
    win_h = max(2, (y_ref - y_top) // n_win)
    x, dx = float(x0), 0.0
    rows, cxs, hits, used = [], [], 0, 0
    for k in range(n_win):
        y1 = y_ref - k * win_h
        y0 = max(y_top, y1 - win_h)
        if y1 <= y0:
            break
        used += 1
        # Windows get narrower toward the horizon (perspective).
        margin = max(3, int(w * 0.05 * (1 - 0.6 * k / n_win)))
        lo, hi = int(max(0, x - margin)), int(min(w, x + margin + 1))
        ys, xs = np.nonzero(ll[y0:y1, lo:hi])
        if xs.size >= 3:
            hits += 1
            for r in np.unique(ys):
                rows.append(y0 + r)
                cxs.append(lo + xs[ys == r].mean())
            new_x = lo + xs.mean()
            if len(rows) > 1:
                dx = 0.5 * dx + 0.5 * (new_x - x)
            x = new_x + dx  # where the line should be in the next (higher) window
        else:
            x += dx  # dash gap: keep going along the line
        if x < 0 or x >= w:
            break
    return np.array(rows, float), np.array(cxs, float), hits / max(1, used)


def _fit_line(rows: np.ndarray, cx: np.ndarray, y_top: int, y_ref: int) -> np.ndarray:
    """x = f(y) as [c2, c1, c0]: a curve only when the points span enough height and the
    curve does not turn back inside the visible range, else a straight line."""
    lin = np.concatenate([[0.0], np.polyfit(rows, cx, 1)])
    span = rows.max() - rows.min()
    if rows.size < 20 or span < (y_ref - y_top) * 0.5:
        return lin
    quad = np.polyfit(rows, cx, 2)
    if quad[0] != 0:
        vertex = -quad[1] / (2 * quad[0])
        if y_top - span * 0.5 <= vertex <= y_ref:
            return lin  # bends back within view: a hook from a few stray points
    return quad


def extract_lanes(drivable: np.ndarray, lanes: np.ndarray, frame: np.ndarray, t: float,
                  min_rows: int = 8) -> ModelLanes:
    """Ego-lane lines from model masks (small) for a full-size frame."""
    H, W = frame.shape[:2]
    h, w = lanes.shape
    sx, sy = W / w, H / h
    hood = hood_row(drivable)
    y_ref = hood if hood is not None else int(h * 0.92)
    road_rows = np.nonzero(drivable[:y_ref + 1].any(axis=1))[0]
    y_top = int(road_rows[0]) if road_rows.size else int(h * 0.45)
    if y_ref - y_top < 10:
        return ModelLanes(t, None, None, (hood + 1) * sy if hood is not None else None, drivable)
    ll = lanes.copy()
    ll[y_ref + 1:] = 0  # nothing below the bonnet line
    # Start points: column histogram over the lower part of the road.
    band = ll[y_ref - int((y_ref - y_top) * 0.4):y_ref + 1]
    hist = np.convolve(band.sum(axis=0).astype(float), np.ones(5) / 5, mode="same")
    starts = _peaks(hist, min_val=0.6, min_gap=max(4, int(w * 0.06)))
    cands = []
    for x0 in starts:
        rows, cx, coverage = _track(ll, x0, y_top, y_ref)
        if rows.size < min_rows or rows.max() - rows.min() < (y_ref - y_top) * 0.25:
            continue
        coef = _fit_line(rows, cx, y_top, y_ref)
        cands.append((float(np.polyval(coef, y_ref)), coef, rows.min(), coverage))
    centre = w / 2
    left = max((c for c in cands if c[0] < centre), key=lambda c: c[0], default=None)
    right = min((c for c in cands if c[0] >= centre), key=lambda c: c[0], default=None)
    gain = road_gain(cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA), drivable, lanes)

    def to_line(c) -> LaneLine | None:
        if c is None:
            return None
        _, coef, y0, coverage = c
        # Rescale x = f(y) from mask to frame pixels: X = sx * f(Y / sy)
        a, b, cc = coef
        full = np.array([a * sx / sy ** 2, b * sx / sy, cc * sx])
        line = LaneLine(full, y0 * sy, (y_ref + 0.5) * sy, dashed=coverage < 0.75)
        line.color = paint_color(frame, line.points(), gain=gain)
        return line

    L, R = to_line(left), to_line(right)
    if L is not None and R is not None and L.x_at(L.y_bottom) >= R.x_at(R.y_bottom):
        L = R = None
    return ModelLanes(t, L, R, (hood + 1) * sy if hood is not None else None, drivable)


class AsyncLaneModel:
    """Runs TwinLiteNet on the latest submitted frame at most every `every_s` seconds.

    `model` may be a TwinLiteNet or a function that builds one; building (compiling for
    the GPU takes ~10 s the first time) then happens on the background thread."""

    def __init__(self, model, every_s: float = 0.5):
        self.model = model
        self.every_s = every_s
        self.result: ModelLanes | None = None
        self.last_ms = 0.0
        self._frame: tuple[float, np.ndarray] | None = None
        self._cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="lane-model", daemon=True)
        self._thread.start()

    def submit(self, frame: np.ndarray, t: float) -> None:
        with self._cv:
            self._frame = (t, frame)
            self._cv.notify()

    def _run(self) -> None:
        if callable(self.model) and not isinstance(self.model, TwinLiteNet):
            try:
                self.model = self.model()
                log.info("lane model ready on %s %s", self.model.device, self.model.input_wh)
            except Exception as e:
                log.warning("lane model disabled: %s", e)
                return
        next_run = 0.0
        while True:
            with self._cv:
                while not self._stop and self._frame is None:
                    self._cv.wait()
                if self._stop:
                    return
                t, frame = self._frame
                self._frame = None
            wait = next_run - time.time()
            if wait > 0:
                time.sleep(wait)
            next_run = time.time() + self.every_s
            try:
                t0 = time.time()
                da, ll = self.model.masks(frame)
                self.result = extract_lanes(da, ll, frame, t)
                self.last_ms = (time.time() - t0) * 1000
            except Exception as e:  # never take the pipeline down
                log.warning("lane model failed: %s", e)

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
