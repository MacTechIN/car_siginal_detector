"""Voice labelling through the real recogniser, without a microphone: the Windows Korean
voice synthesises the phrase to a 16 kHz WAV that is fed to VoiceLabeler's loop."""

import queue
import threading
import time
import wave
from pathlib import Path

import pytest

from csd.config import ROOT

MODEL = ROOT / "models" / "vosk-model-small-ko-0.22"
pytestmark = pytest.mark.skipif(not MODEL.exists(), reason="Vosk model not downloaded")


def synth(text: str, path: Path) -> None:
    pythoncom = pytest.importorskip("pythoncom")
    import win32com.client

    pythoncom.CoInitialize()
    v = win32com.client.Dispatch("SAPI.SpVoice")
    ko = [t for t in v.GetVoices() if "Korean" in t.GetDescription()]
    if not ko:
        pytest.skip("no Korean SAPI voice")
    v.Voice = ko[0]
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # 16 kHz 16-bit mono
    fs = win32com.client.Dispatch("SAPI.SpFileStream")
    fs.Format = fmt
    fs.Open(str(path), 3)
    v.AudioOutputStream = fs
    v.Speak(text)
    fs.Close()


def run_labeler(tmp_path, text: str, muted: bool):
    import vosk

    from csd.voice_label import VoiceLabeler, ascii_model_path

    wav = tmp_path / "say.wav"
    synth(text, wav)
    lab = VoiceLabeler(MODEL, is_muted=lambda: muted)
    model = vosk.Model(ascii_model_path(MODEL))
    audio: queue.Queue[bytes] = queue.Queue()
    with wave.open(str(wav), "rb") as wf:
        while chunk := wf.readframes(4000):
            audio.put(chunk)
    audio.put(b"\x00\x00" * 16000)  # 1 s of silence ends the utterance
    th = threading.Thread(target=lab._loop, args=(model, audio), daemon=True)
    th.start()
    deadline = time.time() + 20
    while not audio.empty() and time.time() < deadline:
        time.sleep(0.05)
    lab._stop.set()
    th.join(5)
    return [lab.labels.get() for _ in range(lab.labels.qsize())]


def test_spoken_label_is_recognised(tmp_path):
    labels = run_labeler(tmp_path, "빨간 좌회전", muted=False)
    assert [lb.label for lb in labels] == ["red_left"]
    assert abs(labels[0].t - time.time()) < 30


def test_audio_is_ignored_while_our_voice_is_speaking(tmp_path):
    assert run_labeler(tmp_path, "빨간불", muted=True) == []
