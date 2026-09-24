"""Renders the monitor with a fake pipeline state (no camera/model needed)."""

import collections
import time
from types import SimpleNamespace

import numpy as np

from csd.dashboard import WIN_H, WIN_W, Dashboard
from csd.detect import Detection
from csd.lanes import LaneResult
from csd.state import EventLog, StateSmoother
from csd.tts import DANGER


def smoother(state):
    s = StateSmoother()
    s.confirmed = state
    return s


def track(light=None, lamps=None, motion=None, plate=None, collision="none", ttc=None):
    return SimpleNamespace(light=smoother(light), lamp_smoother=smoother(lamps), motion_smoother=smoother(motion),
                           plate=plate, collision=collision, ttc=ttc)


def fake_pipe():
    now = time.time()
    events = EventLog(log_dir=None, echo=False)
    events.emit(3, "traffic_light", "red")
    events.emit(7, "car", "brake_on,left_turn", channel="lamps")
    events.emit(7, "license_plate", "12가3456")
    events.emit(7, "car", "collision_danger ttc=1.6s", channel="collision")
    dets = [Detection(3, "traffic_light", (600, 80, 690, 110), 0.8),
            Detection(7, "car", (380, 330, 620, 520), 0.9),
            Detection(9, "truck", (60, 300, 260, 480), 0.8),
            Detection(12, "person", (860, 300, 900, 420), 0.7)]
    polygon = np.array([[60, 768], [470, 420], [560, 420], [980, 768]], np.int32)
    return SimpleNamespace(
        voice=SimpleNamespace(spoken=[(now, "전방 충돌 위험! 브레이크!", DANGER)]),
        tracks={3: track(light="red_left"), 7: track(lamps="brake_on,left_turn", motion="slowing", plate="12가3456",
                                                     collision="danger", ttc=1.6),
                9: track(motion="moving"), 12: track()},
        relevant_light=3, lead_id=7, last_dets=dets,
        last_risk=(now - 1, "collision", "전방 충돌 위험! 브레이크! TTC 1.6s (ID 7)"),
        last_lane=LaneResult((-1.1, 900), (1.2, -300), polygon, True, "none"),
        lane_smoother=smoother("in_lane"), ego_state="moving", fps=4.2, last_frame_w=1024,
        _scene_counts={"vehicles": 2, "person": 1, "two_wheeler": 0},
        paths={7: collections.deque([(500, 560, 0), (505, 540, 0), (500, 520, 0)]),
               9: collections.deque([(300, 500, 0), (220, 490, 0), (160, 480, 0)])},
        events=events,
    )


def test_dashboard_renders_full_layout():
    frame = np.full((768, 1024, 3), 60, np.uint8)
    out = Dashboard().render(frame, fake_pipe().last_dets, fake_pipe())
    assert out.shape == (WIN_H, WIN_W, 3)
    assert out.std() > 10  # something was drawn


def test_dashboard_without_frame_or_state():
    p = fake_pipe()
    p.tracks, p.lead_id, p.relevant_light, p.last_dets, p.last_risk = {}, None, None, [], None
    p.voice.spoken = []
    out = Dashboard().render(None, [], p)
    assert out.shape == (WIN_H, WIN_W, 3)
