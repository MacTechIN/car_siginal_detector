"""Evaluate voice-label recognition without a human speaker.

Every label phrase and a set of everyday sentences are synthesised with the Windows
Korean voice (Heami, 16 kHz WAV) and run through the same Vosk grammar the app uses.
Reports label accuracy and how many chat sentences were wrongly accepted as labels.
Usage: python tools/eval_voice_labels.py
"""
import json, wave, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vosk
from csd.voice_label import PHRASES, DISTRACTORS, parse, ascii_model_path
vosk.SetLogLevel(-1)
m = vosk.Model(ascii_model_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "vosk-model-small-ko-0.22")))
g = json.dumps(list(PHRASES) + DISTRACTORS, ensure_ascii=False)
import win32com.client, pythoncom
pythoncom.CoInitialize()
v = win32com.client.Dispatch("SAPI.SpVoice")
for t in v.GetVoices():
    if "Korean" in t.GetDescription(): v.Voice = t
def synth(text, path):
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat"); fmt.Type = 18
    fs = win32com.client.Dispatch("SAPI.SpFileStream"); fs.Format = fmt
    fs.Open(path, 3); v.AudioOutputStream = fs; v.Speak(text); fs.Close()
labels = list(PHRASES)
chat = ["오늘 날씨가 정말 좋네요", "음악 좀 틀어줘", "저기 편의점 들렀다 가자", "아 배고프다", "신호가 길다",
        "빨리 가자", "앞에 차가 너무 느리다", "전화 좀 받아줘", "라디오 소리 줄여", "주차장 어디야",
        "오른쪽으로 가", "천천히 가", "내일 회사 가야 돼", "배터리 충전해야지", "커피 마시고 싶다"]
import tempfile; tmp = tempfile.mkdtemp()
ok = fp = 0
for i, p in enumerate(labels + chat):
    path = os.path.join(tmp, f"{i}.wav"); synth(p, path)
    wf = wave.open(path, "rb"); rec = vosk.KaldiRecognizer(m, 16000, g)
    while True:
        d = wf.readframes(4000)
        if not d: break
        rec.AcceptWaveform(d)
    text = json.loads(rec.FinalResult())["text"]; lab = parse(text)
    want = PHRASES.get(p)
    if p in PHRASES:
        ok += lab == want
    else:
        fp += lab is not None
    print(f"{'LABEL' if p in PHRASES else 'chat '} {p:16s} -> {text:18s} => {lab}")
print(f"labels correct {ok}/{len(labels)}, chat false labels {fp}/{len(chat)}")
