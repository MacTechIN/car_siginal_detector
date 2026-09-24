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


def draw(frame: np.ndarray, dets, pipe) -> np.ndarray:
    img = frame.copy()
    H, W = img.shape[:2]
    lane = pipe.last_lane
    if lane is not None:
        overlay = img.copy()
        color = (0, 170, 0) if lane.detected else (90, 90, 90)
        if pipe.lane_smoother.confirmed and "departure" in pipe.lane_smoother.confirmed:
            color = (0, 170, 255)
        cv2.fillPoly(overlay, [lane.polygon], color)
        img = cv2.addWeighted(overlay, 0.22, img, 0.78, 0)
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

    for d in dets:
        x1, y1, x2, y2 = (int(v) for v in d.box)
        c = _color(d, pipe)
        cv2.rectangle(img, (x1, y1), (x2, y2), c, 3 if d.tid == pipe.lead_id else 2)
        label = f"{d.tid}" if d.tid >= 0 else "?"
        if d.tid == pipe.lead_id:
            label += " LEAD"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(img, (x1, max(0, y1 - th - 8)), (x1 + tw + 8, y1), c, -1)
        cv2.putText(img, label, (x1 + 4, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)
    return img
