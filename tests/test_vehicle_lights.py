import cv2
import numpy as np

from csd.vehicle_lights import VehicleLightTracker

BOX = (100, 100, 300, 250)  # x1, y1, x2, y2


def car_frame(left_on=False, right_on=False, brake=False, amber=(0, 160, 255)) -> np.ndarray:
    img = np.full((400, 400, 3), 90, np.uint8)
    x1, y1, x2, y2 = BOX
    cv2.rectangle(img, (x1, y1), (x2, y2), (60, 60, 60), -1)
    tail = (40, 30, 200) if brake else (30, 30, 90)  # dim red tail lamps when not braking
    cv2.rectangle(img, (x1 + 5, y1 + 60), (x1 + 50, y1 + 90), tail, -1)
    cv2.rectangle(img, (x2 - 50, y1 + 60), (x2 - 5, y1 + 90), tail, -1)
    if brake:
        cv2.rectangle(img, (x1 + 5, y1 + 60), (x1 + 50, y1 + 90), (30, 30, 255), -1)
        cv2.rectangle(img, (x2 - 50, y1 + 60), (x2 - 5, y1 + 90), (30, 30, 255), -1)
    if left_on:
        cv2.rectangle(img, (x1 + 5, y1 + 92), (x1 + 50, y1 + 108), amber, -1)
    if right_on:
        cv2.rectangle(img, (x2 - 50, y1 + 92), (x2 - 5, y1 + 108), amber, -1)
    return img


def run(seq, fps=10.0):
    trk = VehicleLightTracker()
    out = None
    for i, kw in enumerate(seq):
        out = trk.update(car_frame(**kw), BOX, i / fps)
    return out


def blink(i, fps=10.0, hz=1.5):
    return int(i / fps * hz * 2) % 2 == 0


def test_left_turn_signal():
    st = run([{"left_on": blink(i)} for i in range(25)])
    assert st.turn == "left_turn"


def test_right_turn_signal():
    st = run([{"right_on": blink(i)} for i in range(25)])
    assert st.turn == "right_turn"


def test_hazard_when_both_blink_together():
    st = run([{"left_on": blink(i), "right_on": blink(i)} for i in range(25)])
    assert st.turn == "hazard"


def test_brake_on_after_baseline():
    seq = [{} for _ in range(10)] + [{"brake": True} for _ in range(5)]
    st = run(seq)
    assert st.brake and st.turn == "none"
    assert st.label() == "brake_on"


def test_long_brake_stays_on():
    seq = [{} for _ in range(10)] + [{"brake": True} for _ in range(80)]
    assert run(seq).brake


def test_no_signal():
    st = run([{} for _ in range(25)])
    assert not st.brake and st.turn == "none"
    assert st.label() == "brake_off"
