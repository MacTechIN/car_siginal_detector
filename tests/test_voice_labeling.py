import csv
from pathlib import Path

import numpy as np
import pytest

from csd.discover import find_camera, stream_url
from csd.labeling import LabelSession, square_crop
from csd.voice_label import PHRASES, VoiceLabel, parse

import tools_path  # noqa: F401  (adds tools/ to sys.path)
from build_tl_dataset import build


# ---------------------------------------------------------------- phrase parsing
@pytest.mark.parametrize("text,label", [
    ("빨간불", "red"), ("파란불", "green"), ("초록 좌회전", "green_left"), ("빨간 좌회전", "red_left"),
    ("황색 점멸", "flashing_yellow"), ("신호 없음", "off"), ("취소", "cancel"), ("맞아", "confirm"),
    ("  빨간  좌회전 ", "red_left"),
])
def test_parse_exact_phrases(text, label):
    assert parse(text) == label


@pytest.mark.parametrize("text", ["노란 적색 점멸 점멸", "맞아 초록", "황색 맞아", "라디오 초록불", "", "좌회전 넷"])
def test_parse_rejects_mixed_or_extra_words(text):
    assert parse(text) is None


def test_every_label_phrase_maps_to_known_label():
    known = {"red", "yellow", "green", "left", "green_left", "red_left", "flashing_yellow", "flashing_red", "off",
             "cancel", "confirm"}
    assert set(PHRASES.values()) <= known


# ---------------------------------------------------------------- label session
def frame_with_light():
    f = np.full((300, 400, 3), 40, np.uint8)
    f[50:70, 100:160] = (40, 40, 255)
    return f


def session(tmp_path):
    s = LabelSession(tmp_path / "세션", before_s=1.0, after_s=0.5)  # non-ASCII path on purpose
    for i in range(30):  # 3 s at 10 fps, light visible the whole time
        s.add_frame(100 + i * 0.1, frame_with_light(), (100, 50, 160, 70), 4, "red")
    return s


def test_label_saves_crops_after_window(tmp_path):
    s = session(tmp_path)
    s.submit(VoiceLabel(101.5, "red", "빨간불"))
    assert s.process(101.7) == []                 # window not complete yet
    out = s.process(102.1)
    assert out and "빨간불" in out[0] and "저장" in out[0]
    crops = list((tmp_path / "세션" / "crops" / "red").glob("*.jpg"))
    assert 1 <= len(crops) <= 8 and s.counts["red"] == len(crops)
    rows = list(csv.DictReader(open(tmp_path / "세션" / "labels.csv", encoding="utf-8")))
    assert rows[-1]["label"] == "red" and rows[-1]["track_id"] == "4"


def test_cancel_removes_last_label(tmp_path):
    s = session(tmp_path)
    s.submit(VoiceLabel(101.5, "green", "초록불"))
    s.process(102.5)
    s.submit(VoiceLabel(102.6, "cancel", "취소"))
    out = s.process(102.6)
    assert "취소" in out[0]
    assert not list((tmp_path / "세션" / "crops" / "green").glob("*.jpg"))
    assert s.counts["green"] == 0


def test_confirm_uses_current_prediction(tmp_path):
    s = session(tmp_path)
    s.submit(VoiceLabel(102.8, "confirm", "맞아"))
    out = s.process(102.9)
    assert "빨간불" in out[0]
    assert list((tmp_path / "세션" / "crops" / "red").glob("*.jpg"))


def test_flashing_label_logs_event_without_crops(tmp_path):
    s = session(tmp_path)
    s.submit(VoiceLabel(101.5, "flashing_yellow", "황색 점멸"))
    out = s.process(102.5)
    assert "황색 점멸" in out[0]
    assert not (tmp_path / "세션" / "crops").exists() or not any((tmp_path / "세션" / "crops").rglob("*.jpg"))
    rows = list(csv.DictReader(open(tmp_path / "세션" / "labels.csv", encoding="utf-8")))
    assert rows[-1]["label"] == "flashing_yellow" and rows[-1]["crops"] == "0"


def test_label_without_visible_light(tmp_path):
    s = LabelSession(tmp_path / "s")
    s.add_frame(10.0, frame_with_light(), None, None, None)
    s.submit(VoiceLabel(10.0, "red", "빨간불"))
    out = s.process(11.0)
    assert "보이지 않아" in out[0]


def test_square_crop_keeps_whole_wide_head():
    f = np.zeros((100, 200, 3), np.uint8)
    f[40:50, 20:180] = 255  # 16:1 head
    c = square_crop(f, (20, 40, 180, 50), pad=0.0, size=96)
    assert c.shape == (96, 96, 3)
    cols = np.where(c.max(axis=(0, 2)) > 0)[0]
    assert cols.min() <= 1 and cols.max() >= 94  # nothing cut off left/right


# ---------------------------------------------------------------- dataset builder
def test_dataset_split_is_per_event(tmp_path):
    rec = tmp_path / "recordings"
    for sess in ("s1", "s2"):
        for cls in ("red", "green"):
            d = rec / sess / "crops" / cls
            d.mkdir(parents=True)
            for event in range(5):
                for k in range(4):
                    (d / f"{1000 + event}_{3}_{k}.jpg").write_bytes(b"x")
    out = tmp_path / "ds"
    summary = build(rec, tmp_path / "none", out, val=0.2)
    assert summary["red"]["events"] == 10 and summary["red"]["train"] + summary["red"]["val"] == 40
    for cls in ("red", "green"):
        train_events = {p.name.rsplit("_", 2)[0] for p in (out / "train" / cls).iterdir()}
        val_events = {p.name.rsplit("_", 2)[0] for p in (out / "val" / cls).iterdir()}
        assert train_events and val_events and not (train_events & val_events)


# ---------------------------------------------------------------- camera discovery
def test_stream_url():
    assert stream_url("http://192.168.4.1") == "http://192.168.4.1:81/stream"
    assert stream_url("http://esp32cam.local/") == "http://esp32cam.local:81/stream"


def test_find_camera_falls_back_to_access_point(monkeypatch):
    import csd.discover as dsc
    monkeypatch.setattr(dsc, "is_camera", lambda url, timeout=1.5: url == dsc.AP_URL)
    assert find_camera("http://10.0.0.99", allow_scan=False) == "http://192.168.4.1"


def test_find_camera_none(monkeypatch):
    import csd.discover as dsc
    monkeypatch.setattr(dsc, "is_camera", lambda url, timeout=1.5: False)
    assert find_camera("http://10.0.0.99", allow_scan=False) is None
