import cv2
import numpy as np

from csd.lanes import LaneDetector

W, H = 800, 600


def road(shift=0):
    img = np.full((H, W, 3), 70, np.uint8)
    img[: int(H * 0.5)] = (200, 170, 140)  # sky
    y_top = int(H * 0.6)
    cv2.line(img, (330 + shift, y_top), (80 + shift, H), (255, 255, 255), 8)
    cv2.line(img, (470 + shift, y_top), (720 + shift, H), (255, 255, 255), 8)
    return img


def test_detects_both_lines_and_stays_in_lane():
    det = LaneDetector(horizon_ratio=0.55)
    for _ in range(3):
        r = det.update(road())
    assert r.detected
    assert r.departure == "none"
    xl = r.left[0] * H + r.left[1]
    xr = r.right[0] * H + r.right[1]
    assert abs(xl - 80) < 40 and abs(xr - 720) < 40


def test_departure_right_when_lines_shift_left():
    # Car drifted right: the right line is now close to the image centre at the bottom.
    det = LaneDetector(horizon_ratio=0.55)
    for _ in range(3):
        r = det.update(road(shift=-220))
    assert r.detected and r.departure == "right"


def test_neighbouring_lane_line_is_ignored():
    img = road()
    cv2.line(img, (200, int(H * 0.6)), (-300, H), (255, 255, 255), 8)  # next lane's line
    det = LaneDetector(horizon_ratio=0.55)
    for _ in range(3):
        r = det.update(img)
    assert abs((r.left[0] * H + r.left[1]) - 80) < 40


def test_fallback_polygon_without_lines():
    det = LaneDetector()
    r = det.update(np.full((H, W, 3), 70, np.uint8))
    assert not r.detected and r.polygon.shape == (4, 2)
