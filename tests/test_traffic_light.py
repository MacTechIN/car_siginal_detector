from pathlib import Path

import cv2
import numpy as np
import pytest

from csd.stream import imread_any
from csd.traffic_light import FlashTracker, classify_light

BGR = {"red": (40, 40, 255), "yellow": (0, 200, 255), "green": (160, 255, 40)}


def head(lit: list[str], lamps: int = 3, vertical: bool = False, cell: int = 20) -> np.ndarray:
    """Synthetic Korean signal head: dark housing, lamp cells left->right (or top->bottom)."""
    roles = ["red", "yellow", "left", "green"] if lamps == 4 else ["red", "yellow", "green"]
    img = np.full((cell, cell * lamps, 3), 25, np.uint8)
    for i, role in enumerate(roles):
        center = (i * cell + cell // 2, cell // 2)
        if role in lit:
            color = BGR["green"] if role == "left" else BGR[role]
            cv2.circle(img, center, cell // 2 - 3, color, -1)
        else:
            cv2.circle(img, center, cell // 2 - 3, (45, 45, 45), -1)
    if vertical:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        img = cv2.flip(img, 1)  # red on top
    return img


@pytest.mark.parametrize("lamps,lit,expected", [
    (3, ["red"], "red"),
    (3, ["yellow"], "yellow"),
    (3, ["green"], "green"),
    (3, [], "off"),
    (4, ["red"], "red"),
    (4, ["left"], "left"),
    (4, ["red", "left"], "red_left"),
    (4, ["green", "left"], "green_left"),
    (4, ["green"], "green"),
])
def test_horizontal_heads(lamps, lit, expected):
    r = classify_light(head(lit, lamps))
    assert r.lamps == lamps
    assert r.orientation == "horizontal"
    assert r.state == expected


def test_vertical_head_red_on_top():
    r = classify_light(head(["red"], 3, vertical=True))
    assert r.orientation == "vertical"
    assert r.state == "red"


def test_tiny_or_empty_crop_is_unknown():
    assert classify_light(np.zeros((2, 2, 3), np.uint8)).state == "unknown"


def test_flashing_yellow_detected_from_toggles():
    f = FlashTracker(window_s=3.0, min_toggles=3)
    states = []
    for i in range(30):  # 10 fps, 1 Hz blink
        t = i * 0.1
        s = "yellow" if int(t * 2) % 2 == 0 else "off"
        states.append(f.update(s, t))
    assert states[-1] == "flashing_yellow"


def test_steady_red_is_not_flashing():
    f = FlashTracker()
    assert all(f.update("red", i * 0.1) == "red" for i in range(30))


# Real crops from the 2026-10-06 road recordings (tests/fixtures/tl_crops/<expected>__<note>.png).
# The near-square ones are single lit lamps that used to be read as yellow / red_yellow.
CROPS = sorted((Path(__file__).parent / "fixtures" / "tl_crops").glob("*.png"))


@pytest.mark.parametrize("path", CROPS, ids=[p.stem for p in CROPS])
def test_real_road_crops(path):
    expected = path.stem.split("__")[0]
    r = classify_light(imread_any(path))
    if expected == "none":  # not a signal (a CCTV housing against the sky)
        assert r.state in ("unknown", "off")
    else:
        assert r.state == expected


def test_single_red_lamp_with_white_centre_is_red():
    img = np.full((24, 24, 3), 30, np.uint8)
    cv2.circle(img, (12, 12), 10, (40, 40, 230), -1)   # red halo
    cv2.circle(img, (12, 12), 5, (250, 250, 255), -1)  # over-exposed centre
    assert classify_light(img).state == "red"


def test_impossible_lamp_combination_is_unknown():
    assert classify_light(head(["red", "yellow"], 3)).state == "unknown"
    assert classify_light(head(["red", "yellow", "green"], 3)).state == "unknown"


def test_lane_control_row_found():
    from csd.traffic_light import lane_control_row
    # 2026-10-06 17:44:27: three green down-arrows on a highway gantry (+ a vehicle head elsewhere)
    boxes = {455: (386, 230, 402, 247), 456: (446, 228, 462, 244), 458: (331, 233, 347, 249),
             900: (396, 231, 494, 261)}
    assert lane_control_row(boxes) == {455, 456, 458}


def test_two_signal_heads_are_not_a_lane_control_row():
    from csd.traffic_light import lane_control_row
    assert lane_control_row({1: (396, 231, 494, 261), 2: (526, 370, 576, 391)}) == set()
    # two single lamps only
    assert lane_control_row({1: (100, 50, 116, 66), 2: (200, 50, 216, 66)}) == set()
