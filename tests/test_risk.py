import math

import numpy as np

from csd.risk import RiskAnalyzer, lane_offset

W, H = 1000, 750
# Ego lane polygon: bottom-left, top-left, top-right, bottom-right
POLY = np.array([[100, 750], [440, 410], [560, 410], [900, 750]], np.int32)


def box_at(cx, bottom, w, h=None):
    h = h or w * 0.8
    return (cx - w / 2, bottom - h, cx + w / 2, bottom)


def test_lane_offset_centre_and_edges():
    assert abs(lane_offset(box_at(500, 740, 100), POLY, H)) < 0.05
    assert lane_offset(box_at(150, 740, 100), POLY, H) < -0.8
    assert lane_offset(box_at(870, 740, 100), POLY, H) > 0.8


def test_ttc_from_scale_growth():
    r = RiskAnalyzer()
    true_ttc = 2.0  # width grows as 1/(TTC - t)
    for i in range(10):
        t = i * 0.1
        w = 200 * true_ttc / (true_ttc - t)
        r.update_track(1, box_at(500, 700, w), t, POLY, H)
    ttc = r.kin[1].ttc()
    # at t=0.9 the remaining TTC is 1.1 s; the windowed fit lags slightly
    assert ttc is not None and 0.9 < ttc < 1.6
    level, _ = r.collision_level(1, box_at(500, 700, 300), W)
    assert level == "danger"


def test_no_ttc_when_receding():
    r = RiskAnalyzer()
    for i in range(10):
        r.update_track(1, box_at(500, 700, 200 * math.exp(-0.2 * i * 0.1)), i * 0.1, POLY, H)
    assert r.kin[1].ttc() is None


def test_lead_is_closest_in_lane():
    r = RiskAnalyzer()
    vehicles = {1: box_at(500, 600, 120), 2: box_at(500, 700, 200), 3: box_at(150, 740, 200)}
    assert r.select_lead(vehicles, POLY, H) == 2


def test_lead_starting_when_ego_stopped():
    r = RiskAnalyzer()
    states = []
    for i in range(15):
        t = i * 0.1
        w = 300 * math.exp(-0.15 * t) if t > 0.3 else 300
        r.update_track(1, box_at(500, 700, w), t, POLY, H)
        states.append(r.lead_state(1, "stopped", t))
    assert states[-1] == "starting"


def test_lead_stopped_when_both_still():
    r = RiskAnalyzer()
    for i in range(12):
        r.update_track(1, box_at(500, 700, 300), i * 0.1, POLY, H)
    assert r.lead_state(1, "stopped", 1.1) == "stopped"


def test_cut_in_from_left():
    r = RiskAnalyzer()
    side = None
    # A car ahead in the left lane (lane is ~420 px wide at y=560) for 1 s,
    # then moving into our lane
    for i in range(20):
        t = i * 0.1
        cx = 180 if t < 1.0 else 180 + (t - 1.0) * 400
        r.update_track(5, box_at(cx, 560, 150), t, POLY, H)
        side = r.cut_in(5, box_at(cx, 560, 150), W, t) or side
    assert side == "cut_in_left"


def test_no_cut_in_for_car_staying_beside():
    r = RiskAnalyzer()
    for i in range(20):
        t = i * 0.1
        r.update_track(5, box_at(180, 560, 150), t, POLY, H)
        assert r.cut_in(5, box_at(180, 560, 150), W, t) is None
