"""Camera-view overlays: ego lane + heading, object boxes with ids, movement trails."""

from __future__ import annotations

import cv2
import numpy as np

VEHICLE_COLOR = (80, 220, 80)
LEAD_COLOR = (40, 40, 255)
LIGHT_COLOR = (0, 200, 255)
PERSON_COLOR = (255, 150, 30)
DANGER_COLOR = (0, 0, 255)


def _color(d, pipe):
    if d.tid == pipe.lead_id:
        ts = pipe.tracks.get(d.tid)
        return DANGER_COLOR if ts is not None and ts.collision == "danger" else LEAD_COLOR
    if d.name == "traffic_light":
        return LIGHT_COLOR
    if d.name in ("person", "bicycle"):
        return PERSON_COLOR
    return VEHICLE_COLOR


# Lane paint as drawn on the video (BGR)
PAINT = {"white": (255, 255, 255), "yellow": (0, 215, 255), "blue": (255, 140, 0)}


def _lane_area(lane, H: int) -> np.ndarray:
    """Ego-lane polygon to fill: the model's curves when present, else the straight one;
    cut at the bonnet line."""
    bottom = lane.hood_y or H
    if lane.left_line is not None and lane.right_line is not None:
        top = max(lane.left_line.y_top, lane.right_line.y_top)
        ys = np.linspace(top, min(bottom, lane.left_line.y_bottom, lane.right_line.y_bottom), 16)
        left = np.stack([lane.left_line.x_at(ys), ys], 1)
        right = np.stack([lane.right_line.x_at(ys), ys], 1)[::-1]
        return np.concatenate([left, right]).astype(np.int32)
    poly = lane.polygon.astype(float).copy()
    if bottom < H:
        (lbx, lby), (ltx, lty), (rtx, rty), (rbx, rby) = poly
        t = (bottom - lty) / max(lby - lty, 1e-6)
        poly[0] = (ltx + (lbx - ltx) * t, bottom)
        poly[3] = (rtx + (rbx - rtx) * t, bottom)
    return poly.astype(np.int32)


def _draw_paint_line(img: np.ndarray, line, W: int) -> None:
    """A lane line in its paint colour; dashed paint is drawn dashed."""
    color = PAINT.get(line.color, PAINT["white"])
    thick = max(2, int(4 * W / 1024))
    pts = line.points(32)
    if not line.dashed:
        cv2.polylines(img, [pts], False, (0, 0, 0), thick + 2, cv2.LINE_AA)  # outline for contrast
        cv2.polylines(img, [pts], False, color, thick, cv2.LINE_AA)
        return
    for i in range(0, len(pts) - 1, 4):  # 2 segments on, 2 off
        seg = pts[i:i + 3]
        cv2.polylines(img, [seg], False, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.polylines(img, [seg], False, color, thick, cv2.LINE_AA)


def draw(frame: np.ndarray, dets, pipe) -> np.ndarray:
    img = frame.copy()
    H, W = img.shape[:2]
    lane = pipe.last_lane
    if lane is not None:
        overlay = img.copy()
        color = (0, 170, 0) if lane.detected else (90, 90, 90)
        if pipe.lane_smoother.confirmed and "departure" in pipe.lane_smoother.confirmed:
            color = (0, 170, 255)
        area = _lane_area(lane, H)
        cv2.fillPoly(overlay, [area], color)
        img = cv2.addWeighted(overlay, 0.22, img, 0.78, 0)
        for line in (lane.left_line, lane.right_line):
            if line is not None:
                _draw_paint_line(img, line, W)
        # Ego heading: arrow along the lane centre line
        (lbx, lby), (ltx, lty), (rtx, rty), (rbx, rby) = lane.polygon
        bottom = (int((lbx + rbx) / 2), int(H - 5))
        top = (int((ltx + rtx) / 2), int(lty + (H - lty) * 0.35))
        cv2.arrowedLine(img, bottom, top, (255, 255, 255), 3, cv2.LINE_AA, tipLength=0.08)

    # Movement trails
    for d in dets:
        path = pipe.paths.get(d.tid)
        if path and len(path) >= 2 and d.name != "traffic_light":
            pts = np.array([(int(x), int(y)) for x, y, _ in path], np.int32)
            c = _color(d, pipe)
            for i in range(1, len(pts)):
                thick = 1 + 3 * i // len(pts)
                cv2.line(img, tuple(pts[i - 1]), tuple(pts[i]), c, thick, cv2.LINE_AA)

    # Small id tags, sized to the frame width so they look the same at XGA and UXGA once the
    # view is scaled to the monitor.
    font = 0.38 * W / 1024
    pad = max(2, int(3 * W / 1024))
    for d in dets:
        x1, y1, x2, y2 = (int(v) for v in d.box)
        c = _color(d, pipe)
        cv2.rectangle(img, (x1, y1), (x2, y2), c, 3 if d.tid == pipe.lead_id else 2)
        label = f"{d.tid}" if d.tid >= 0 else "?"
        if d.tid == pipe.lead_id:
            label += " LEAD"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font, 1)
        cv2.rectangle(img, (x1, max(0, y1 - th - 2 * pad)), (x1 + tw + 2 * pad, y1), c, -1)
        cv2.putText(img, label, (x1 + pad, y1 - pad), cv2.FONT_HERSHEY_SIMPLEX, font, (0, 0, 0), 1, cv2.LINE_AA)
    return img
