"""Hands-free voice labelling for Korean traffic-light training data.

While driving, the driver says what the light shows ("빨간불", "좌회전", "초록 좌회전" ...).
Offline Korean speech recognition (Vosk, grammar mode) turns it into a class label; the
pipeline then saves traffic-light crops from the frames around that moment.

Grammar mode alone forces *any* speech into one of the phrases (a radio or a chat would
become labels), so:
  - the grammar also contains a list of everyday "distractor" words that absorb
    non-label speech, and
  - only an utterance that is exactly one label phrase is accepted.
Audio captured while our own TTS is speaking is discarded (the mic hears the speaker).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# spoken phrase -> label (commands: cancel = undo last label, confirm = accept current prediction)
PHRASES: dict[str, str] = {
    "빨간불": "red", "적색": "red",
    "노란불": "yellow", "노란": "yellow", "황색": "yellow",
    "초록불": "green", "초록": "green", "파란불": "green", "녹색": "green",
    "좌회전": "left",
    "초록 좌회전": "green_left", "파란 좌회전": "green_left", "직진 좌회전": "green_left",
    "빨간 좌회전": "red_left",
    "노란 점멸": "flashing_yellow", "황색 점멸": "flashing_yellow",
    "빨간 점멸": "flashing_red", "적색 점멸": "flashing_red",
    "신호 없음": "off",
    "취소": "cancel",
    "맞아": "confirm",
}

# Everyday words that non-label speech is recognised as instead of a label phrase.
# Words missing from the model vocabulary are ignored by Vosk.
DISTRACTORS = (
    "아 어 음 네 예 아니 응 그래 그냥 진짜 정말 너무 많이 조금 좀 이거 저거 그거 여기 저기 거기 "
    "오늘 내일 어제 지금 나중 다음 먼저 빨리 천천히 같이 혼자 우리 너 나 저 그 이 "
    "밥 물 커피 음악 노래 라디오 전화 문자 사람 친구 엄마 아빠 회사 집 학교 가게 편의점 주유소 "
    "날씨 바람 덥다 춥다 좋다 싫다 배고프다 졸리다 피곤하다 "
    "가자 가 와 보자 봐 해 하자 했어 있어 없어 몰라 알아 할까 어디 언제 왜 뭐 누구 "
    "도로 차선 앞 뒤 옆 오른쪽 왼쪽 유턴 주차 속도 카메라 "
    "시간 잠깐 그만 계속 다시 시작 "
    "좋네 좋아 괜찮아 미안 고마워 감사 안녕 여보세요 이제 아직 벌써 그런데 그러면 그리고 근데"
).split()


@dataclass
class VoiceLabel:
    t: float        # wall-clock time the utterance started
    label: str      # red, yellow, ... or cancel / confirm
    heard: str      # recognised text


def parse(text: str) -> str | None:
    """Label for an utterance only if it is exactly one label phrase."""
    return PHRASES.get(" ".join(text.split()))


def ascii_model_path(path: str | Path) -> str:
    """Vosk (Kaldi) cannot open a model under a non-ASCII absolute path (e.g. a Korean
    user folder). Use a relative path if that is ASCII, else a copy under %PUBLIC%."""
    path = Path(path).resolve()
    if str(path).isascii():
        return str(path)
    rel = os.path.relpath(path)
    if rel.isascii():
        return rel
    target = Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "csd_models" / path.name
    if not (target / "am" / "final.mdl").exists():
        log.info("copying Vosk model to ASCII path %s", target)
        shutil.copytree(path, target, dirs_exist_ok=True)
    return str(target)


class VoiceLabeler:
    """Microphone -> Vosk -> VoiceLabel queue, on a background thread."""

    def __init__(self, model_dir: str | Path, is_muted=lambda: False, device=None, sample_rate: int = 16000):
        self.model_dir = model_dir
        self.is_muted = is_muted
        self.device = device
        self.sample_rate = sample_rate
        self.labels: queue.Queue[VoiceLabel] = queue.Queue()
        self.heard_log: list[tuple[float, str, str | None]] = []  # (t, text, label) for the monitor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.listening = False

    def grammar(self) -> str:
        return json.dumps(list(PHRASES) + DISTRACTORS, ensure_ascii=False)

    def start(self) -> "VoiceLabeler":
        self._thread = threading.Thread(target=self._run, name="voice-label", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            import sounddevice as sd
            import vosk

            vosk.SetLogLevel(-1)
            model = vosk.Model(ascii_model_path(self.model_dir))
            audio: queue.Queue[bytes] = queue.Queue()

            def cb(indata, frames, t, status):
                audio.put(bytes(indata))

            with sd.RawInputStream(samplerate=self.sample_rate, blocksize=4000, dtype="int16", channels=1,
                                   device=self.device, callback=cb):
                self.listening = True
                log.info("voice labelling: listening")
                self._loop(model, audio)
        except Exception as e:
            self.error = str(e)
            log.warning("voice labelling unavailable: %s", e)
        finally:
            self.listening = False

    def _new_rec(self, model):
        import vosk

        rec = vosk.KaldiRecognizer(model, self.sample_rate, self.grammar())
        rec.SetWords(True)
        return rec

    def _loop(self, model, audio: queue.Queue) -> None:
        rec = self._new_rec(model)
        t0 = time.time()          # wall time of the recogniser's audio position 0
        fed = 0.0                 # seconds of audio fed since t0
        muted = False
        while not self._stop.is_set():
            try:
                chunk = audio.get(timeout=0.2)
            except queue.Empty:
                continue
            dur = len(chunk) / 2 / self.sample_rate
            if self.is_muted():
                # Drop our own voice alerts; restart recognition cleanly afterwards.
                if not muted:
                    rec, muted = self._new_rec(model), True
                continue
            if muted:
                muted = False
                t0, fed = time.time() - dur, 0.0
            fed += dur
            if rec.AcceptWaveform(chunk):
                self._handle(json.loads(rec.Result()), t0)
        self._handle(json.loads(rec.FinalResult()), t0)

    def _handle(self, result: dict, t0: float) -> None:
        text = result.get("text", "").strip()
        if not text:
            return
        words = result.get("result") or []
        t_start = t0 + (words[0]["start"] if words else 0.0)
        label = parse(text)
        self.heard_log.append((time.time(), text, label))
        del self.heard_log[:-20]
        if label:
            log.info("voice label: %s (%s)", label, text)
            self.labels.put(VoiceLabel(t_start, label, text))
        else:
            log.debug("voice ignored: %s", text)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
