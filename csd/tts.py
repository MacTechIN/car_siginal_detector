"""Korean voice alerts with priorities (Windows SAPI5, offline).

One worker thread owns the SAPI voice. Alerts are ordered by priority (0 = most urgent);
an alert more urgent than the one being spoken purges it (SVSFPurgeBeforeSpeak).
The same key is not repeated within its cooldown, and queued alerts that waited
longer than `max_age_s` are dropped so stale information is never read out.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Priorities
DANGER, WARNING, SIGNAL, INFO = 0, 1, 2, 3

SVSF_ASYNC = 1
SVSF_PURGE = 2


@dataclass(order=True)
class Alert:
    priority: int
    seq: int
    text: str = field(compare=False)
    key: str = field(compare=False)
    created: float = field(compare=False)


class SapiSpeaker:
    """Thin SAPI5 wrapper. Must be created and used on the same thread (COM apartment)."""

    def __init__(self, voice_hint: str = "Korean", rate: int = 1, volume: int = 100):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.voice = win32com.client.Dispatch("SAPI.SpVoice")
        for tok in self.voice.GetVoices():
            if voice_hint.lower() in tok.GetDescription().lower():
                self.voice.Voice = tok
                break
        self.voice.Rate = rate
        self.voice.Volume = volume
        log.info("TTS voice: %s", self.voice.Voice.GetDescription())

    def speak(self, text: str, interrupt: bool) -> None:
        self.voice.Speak(text, SVSF_ASYNC | (SVSF_PURGE if interrupt else 0))

    def busy(self) -> bool:
        return self.voice.Status.RunningState == 2  # SRSEIsSpeaking

    def stop(self) -> None:
        self.voice.Speak("", SVSF_ASYNC | SVSF_PURGE)


class NullSpeaker:
    """Used when TTS is disabled; records what would have been said."""

    def __init__(self):
        self.spoken: list[str] = []

    def speak(self, text: str, interrupt: bool) -> None:
        self.spoken.append(text)

    def busy(self) -> bool:
        return False

    def stop(self) -> None:
        pass


class AlertQueue:
    def __init__(self, speaker_factory=None, cooldown_s: float = 8.0, max_age_s: float = 2.5):
        self.speaker_factory = speaker_factory or (lambda: SapiSpeaker())
        self.cooldown_s = cooldown_s
        self.max_age_s = max_age_s
        self._heap: list[Alert] = []
        self._seq = itertools.count()
        self._last_said: dict[str, float] = {}
        self._cv = threading.Condition()
        self._current_priority = 99
        self._stop = False
        self._thread: threading.Thread | None = None
        self.speaker = None
        # (time, text, priority) of alerts actually spoken, newest last (shown as captions)
        self.spoken: list[tuple[float, str, int]] = []
        self._last_busy = 0.0

    def start(self) -> "AlertQueue":
        ready = threading.Event()

        def run():
            try:
                self.speaker = self.speaker_factory()
            except Exception as e:  # no SAPI / no voice: keep running silently
                log.warning("TTS unavailable (%s); alerts will be logged only", e)
                self.speaker = NullSpeaker()
            ready.set()
            self._loop()

        self._thread = threading.Thread(target=run, name="tts", daemon=True)
        self._thread.start()
        ready.wait(10)
        return self

    def say(self, text: str, priority: int = INFO, key: str | None = None, cooldown_s: float | None = None) -> bool:
        """Queue an alert. Returns False when suppressed by the per-key cooldown."""
        key = key or text
        now = time.time()
        cd = self.cooldown_s if cooldown_s is None else cooldown_s
        with self._cv:
            if now - self._last_said.get(key, -1e9) < cd:
                return False
            self._last_said[key] = now
            # Only the newest alert per key matters.
            self._heap = [a for a in self._heap if a.key != key]
            heapq.heapify(self._heap)
            heapq.heappush(self._heap, Alert(priority, next(self._seq), text, key, now))
            self._cv.notify()
        return True

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._heap and not self._stop:
                    self._cv.wait(0.1)
                    if self.speaker and not self.speaker.busy():
                        self._current_priority = 99
                    elif self.speaker:
                        self._last_busy = time.time()
                if self._stop:
                    return
                alert = heapq.heappop(self._heap)
            if time.time() - alert.created > self.max_age_s:
                continue
            busy = self.speaker.busy()
            if busy and alert.priority >= self._current_priority:
                # Less or equally urgent: wait for the current sentence to end.
                while self.speaker.busy() and not self._stop:
                    self._last_busy = time.time()
                    time.sleep(0.05)
                    with self._cv:
                        if self._heap and self._heap[0].priority < alert.priority:
                            break  # something more urgent arrived; re-queue this one
                with self._cv:
                    if self._heap and self._heap[0].priority < alert.priority:
                        heapq.heappush(self._heap, alert)
                        continue
                if time.time() - alert.created > self.max_age_s:
                    continue
                busy = False
            self._current_priority = alert.priority
            log.debug("TTS[%d] %s", alert.priority, alert.text)
            self.spoken.append((time.time(), alert.text, alert.priority))
            del self.spoken[:-50]
            self._last_busy = time.time()
            try:
                self.speaker.speak(alert.text, interrupt=busy)
            except Exception as e:
                log.warning("TTS speak failed: %s", e)

    def speaking_recently(self, margin_s: float = 0.8) -> bool:
        """True while an alert is being spoken or ended less than `margin_s` ago
        (used to keep the voice-label microphone from hearing our own alerts)."""
        # Only the TTS thread touches the SAPI object (COM apartment); it keeps
        # _last_busy up to date while speaking, polling every 0.1 s.
        now = time.time()
        if self.spoken and now - self.spoken[-1][0] < 0.3:
            return True  # just handed to the speaker, may not report busy yet
        return now - self._last_busy < margin_s + 0.15

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        if self._thread:
            self._thread.join(2)
