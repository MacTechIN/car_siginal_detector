import threading
import time

from csd.tts import DANGER, INFO, SIGNAL, AlertQueue


class FakeSpeaker:
    """Speaks for `dur` seconds; records (text, interrupted)."""

    def __init__(self, dur=0.3):
        self.dur = dur
        self.log = []
        self._until = 0.0
        self.lock = threading.Lock()

    def speak(self, text, interrupt):
        with self.lock:
            self.log.append((text, interrupt))
            self._until = time.time() + self.dur

    def busy(self):
        return time.time() < self._until

    def stop(self):
        self._until = 0.0


def make(dur=0.3, **kw):
    sp = FakeSpeaker(dur)
    q = AlertQueue(lambda: sp, **kw).start()
    return q, sp


def test_cooldown_suppresses_repeats():
    q, sp = make(dur=0.05, cooldown_s=5)
    assert q.say("빨간불입니다", SIGNAL, key="tl")
    assert not q.say("빨간불입니다", SIGNAL, key="tl")
    time.sleep(0.3)
    q.close()
    assert [t for t, _ in sp.log] == ["빨간불입니다"]


def test_urgent_alert_interrupts_info():
    q, sp = make(dur=1.0, cooldown_s=0)
    q.say("주변 차량 3대", INFO, key="scene")
    time.sleep(0.2)
    q.say("전방 충돌 위험! 브레이크!", DANGER, key="fcw")
    time.sleep(0.3)
    q.close()
    assert sp.log[0] == ("주변 차량 3대", False)
    assert sp.log[1] == ("전방 충돌 위험! 브레이크!", True)


def test_less_urgent_waits_then_expires_if_stale():
    q, sp = make(dur=1.0, cooldown_s=0, max_age_s=0.5)
    q.say("전방 충돌 주의", DANGER, key="fcw")
    time.sleep(0.1)
    q.say("주변 차량 3대", INFO, key="scene")  # waits behind a 1 s sentence -> older than 0.5 s
    time.sleep(1.3)
    q.close()
    assert [t for t, _ in sp.log] == ["전방 충돌 주의"]
