from csd.state import EventLog, StateSmoother, format_event


def test_format_matches_spec():
    assert format_event(3, "traffic_light", "red") == '3 : [traffic_light] : "red"'


def test_smoother_needs_hold_time_and_majority():
    s = StateSmoother(window_s=1.0, hold_s=0.3)
    assert s.update("red", 0.0) is None          # candidate, not yet held
    assert s.update("red", 0.2) is None
    assert s.update("red", 0.35) == "red"        # held 0.35 s -> confirmed
    assert s.update("red", 0.5) is None          # unchanged -> no event
    # A single flicker frame does not change the state
    assert s.update("off", 0.6) is None
    assert s.update("red", 0.7) is None
    assert s.confirmed == "red"


def test_smoother_switches_after_new_majority_holds():
    s = StateSmoother(window_s=0.6, hold_s=0.3)
    for t in (0.0, 0.1, 0.2, 0.3, 0.4):
        s.update("red", t)
    out = [s.update("green", t) for t in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2)]
    assert "green" in out
    assert s.confirmed == "green"


def test_event_log_dedup_per_channel(capsys):
    ev = EventLog(log_dir=None)
    assert ev.emit(7, "car", "brake_on", channel="lamps")
    assert ev.emit(7, "car", "lead_moving", channel="motion")
    assert ev.emit(7, "car", "brake_on", channel="lamps") is None  # same channel, same state
    assert ev.emit(7, "car", "brake_off", channel="lamps")
    out = capsys.readouterr().out.splitlines()
    assert out == ['7 : [car] : "brake_on"', '7 : [car] : "lead_moving"', '7 : [car] : "brake_off"']
