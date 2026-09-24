"""Per-object state smoothing and the `id : [name] : "state"` event log."""

from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path


def format_event(obj_id: int | str, name: str, state: str) -> str:
    """Format one log line exactly as the spec requires: id : [name] : "state"."""
    return f'{obj_id} : [{name}] : "{state}"'


class StateSmoother:
    """Confirms a label only after it wins a weighted vote over a time window.

    A new state must hold the majority for `hold_s` seconds before it replaces the
    confirmed one (hysteresis), which suppresses single-frame flicker from LED PWM,
    JPEG noise and detector jitter.
    """

    def __init__(self, window_s: float = 1.0, hold_s: float = 0.4, min_share: float = 0.6):
        self.window_s = window_s
        self.hold_s = hold_s
        self.min_share = min_share
        self._samples: collections.deque[tuple[float, str, float]] = collections.deque()
        self.confirmed: str | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0

    def update(self, label: str | None, t: float, weight: float = 1.0) -> str | None:
        """Add an observation; returns the new confirmed state when it changes, else None."""
        if label is not None:
            self._samples.append((t, label, weight))
        while self._samples and t - self._samples[0][0] > self.window_s:
            self._samples.popleft()
        if not self._samples:
            return None

        votes: dict[str, float] = collections.defaultdict(float)
        for _, lab, w in self._samples:
            votes[lab] += w
        best, best_w = max(votes.items(), key=lambda kv: kv[1])
        if best_w / sum(votes.values()) < self.min_share or best == self.confirmed:
            self._candidate = None
            return None

        if best != self._candidate:
            self._candidate, self._candidate_since = best, t
        if t - self._candidate_since >= self.hold_s:
            self.confirmed, self._candidate = best, None
            return best
        return None


@dataclass
class Event:
    obj_id: int | str
    name: str
    state: str
    t: float = field(default_factory=time.time)

    def line(self) -> str:
        return format_event(self.obj_id, self.name, self.state)


class EventLog:
    """Prints confirmed state changes to the console and appends them to a log file."""

    def __init__(self, log_dir: str | Path | None = "logs", echo: bool = True):
        self.echo = echo
        self._last: dict[tuple[int | str, str], str] = {}
        self._file = None
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            name = time.strftime("events_%Y%m%d_%H%M%S.log")
            self._file = open(Path(log_dir) / name, "a", encoding="utf-8")
        self.history: list[Event] = []

    def emit(self, obj_id: int | str, name: str, state: str, force: bool = False,
             channel: str | None = None) -> Event | None:
        """Log a state; a state equal to the last one on the same (object, channel) is dropped
        unless forced. Channels keep independent facts about one object (lamps, motion,
        collision) from overwriting each other's last value."""
        key = (obj_id, channel or name)
        if not force and self._last.get(key) == state:
            return None
        self._last[key] = state
        ev = Event(obj_id, name, state)
        self.history.append(ev)
        if len(self.history) > 1000:
            del self.history[:500]
        if self.echo:
            print(ev.line(), flush=True)
        if self._file:
            ts = time.strftime("%H:%M:%S", time.localtime(ev.t)) + f".{int(ev.t * 1000) % 1000:03d}"
            self._file.write(f"{ts} {ev.line()}\n")
            self._file.flush()
        logging.getLogger("csd.event").debug(ev.line())
        return ev

    def forget(self, obj_id: int | str) -> None:
        for key in [k for k in self._last if k[0] == obj_id]:
            del self._last[key]

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
