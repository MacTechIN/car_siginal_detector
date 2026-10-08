import numpy as np
import cv2
import pytest

from csd.lane_model import LaneLine, extract_lanes, hood_row, paint_color

W, H = 1024, 768
h, w = 192, 320  # model mask size


def masks(hood: int = 150, left_dashed: bool = False):
    """Synthetic model output: road (drivable) above the bonnet row, two lane lines."""
    da = np.zeros((h, w), np.uint8)
    da[90:hood] = 1
    ll = np.zeros((h, w), np.uint8)
    for y in range(90, hood):
        t = (y - 90) / (hood - 90)
        if not left_dashed or (y // 8) % 2 == 0:
            ll[y, int(150 - 110 * t):int(150 - 110 * t) + 3] = 1   # left line
        ll[y, int(170 + 110 * t):int(170 + 110 * t) + 3] = 1        # right line
        ll[y, int(130 - 230 * t) if 130 - 230 * t > 0 else 0] = 1   # next lane's line (further out)
    return da, ll


def frame_with_paint(left_bgr, right_bgr):
    img = np.full((H, W, 3), 70, np.uint8)
    sx, sy = W / w, H / h
    for y in range(90, 150):
        t = (y - 90) / 60
        cv2.circle(img, (int((151 - 110 * t) * sx), int(y * sy)), 6, left_bgr, -1)
        cv2.circle(img, (int((171 + 110 * t) * sx), int(y * sy)), 6, right_bgr, -1)
    return img


def test_hood_row_is_bottom_of_drivable_area():
    da, _ = masks(hood=150)
    assert hood_row(da) == 149
    assert hood_row(np.zeros((h, w), np.uint8)) is None


def test_ego_lines_nearest_centre_with_colour_and_dash():
    da, ll = masks(left_dashed=True)
    img = frame_with_paint((0, 200, 255), (250, 250, 250))  # yellow left, white right (BGR)
    r = extract_lanes(da, ll, img, t=1.0)
    assert r.left is not None and r.right is not None
    yb = r.left.y_bottom
    assert r.left.x_at(yb) == pytest.approx(41 * W / w, abs=20)    # inner line, not the next lane's
    assert r.right.x_at(yb) == pytest.approx(281 * W / w, abs=20)
    assert r.hood_y == pytest.approx(150 * H / h, abs=5)
    assert (r.left.color, r.left.dashed) == ("yellow", True)
    assert (r.right.color, r.right.dashed) == ("white", False)


def test_flat_blobs_ignored_and_crossing_lines_rejected():
    da, _ = masks()
    ll = np.zeros((h, w), np.uint8)
    ll[120:123, 60:260] = 1  # stop line
    r = extract_lanes(da, ll, np.zeros((H, W, 3), np.uint8), 0.0)
    assert r.left is None and r.right is None


def test_paint_colour_blue_bus_lane():
    img = np.full((100, 100, 3), 60, np.uint8)
    cv2.line(img, (50, 0), (50, 99), (220, 120, 20), 5)  # blue (BGR)
    pts = np.array([[50, 0], [50, 99]], np.int32)
    assert paint_color(img, pts) == "blue"


def test_linear_approximation_of_curve():
    line = LaneLine(np.array([0.0, -0.5, 600.0]), 400, 700)
    a, b = line.linear()
    assert a == pytest.approx(-0.5) and b == pytest.approx(600.0)


def test_dusk_blue_cast_on_white_paint_is_white():
    from csd.lane_model import road_gain
    img = np.full((60, 60, 3), (110, 80, 70), np.uint8)       # bluish asphalt (BGR)
    cv2.line(img, (30, 0), (30, 59), (255, 200, 170), 5)       # white paint, same cast
    small_da = np.ones((60, 60), np.uint8)
    small_ll = np.zeros((60, 60), np.uint8)
    small_ll[:, 27:34] = 1
    pts = np.array([[30, 0], [30, 59]], np.int32)
    assert paint_color(img, pts, gain=road_gain(img, small_da, small_ll)) == "white"


def test_hook_shaped_points_fall_back_to_straight_line():
    from csd.lane_model import _fit_line
    rows = np.arange(100, 150, dtype=float)
    cx = 50 + 0.8 * (rows - 100)
    cx[:6] = [90, 80, 70, 62, 56, 52]  # a few stray points bending away at the top
    coef = _fit_line(rows, cx, 90, 149)
    assert coef[0] == 0.0  # straight
