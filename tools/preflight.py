"""Pre-drive check: run this in the car before starting the demo.

Checks power, disk, models, Korean voice, microphone, and finds the camera (home Wi-Fi,
the camera's own "CSD-CAM" access point, esp32cam.local or a subnet scan), measures the
stream frame rate, then reads the result out loud.

Usage: python tools/preflight.py [--no-voice]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from csd import config as cfgmod  # noqa: E402

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def check_power():
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return OK, "전원: 배터리 정보 없음 (데스크톱?)"
    if b.power_plugged:
        return OK, f"전원: 연결됨 ({b.percent:.0f}%)"
    level = FAIL if b.percent < 30 else WARN
    return level, f"전원: 배터리 {b.percent:.0f}% 사용 중. 충전기를 연결하세요 (배터리 모드는 CPU가 느려집니다)"


def check_disk():
    free = shutil.disk_usage(ROOT).free / 1e9
    level = OK if free > 5 else WARN if free > 1 else FAIL
    return level, f"디스크: 여유 {free:.1f} GB (녹화 1시간 약 0.8 GB)"


def check_models(cfg):
    need = {
        "검출 모델": cfgmod.resolve(cfg["detector"]["model"]),
        "번호판 모델": cfgmod.resolve(cfg["plate"]["model_dir"]) / "syllable_detect_v1.pt",
        "음성인식 모델": cfgmod.resolve(cfg.get("voice_label", {}).get("model", "models/vosk-model-small-ko-0.22")),
    }
    missing = [k for k, p in need.items() if not p.exists()]
    tl = cfgmod.resolve(cfg["traffic_light"].get("classifier", "models/tl_cls.pt"))
    extra = "학습된 신호등 분류기 사용" if tl.exists() else "신호등은 규칙 기반 판별 (학습 모델 없음)"
    if missing:
        return FAIL, f"모델 없음: {', '.join(missing)} -> python tools/fetch_models.py"
    return OK, f"모델: 모두 있음. {extra}"


def check_voice(cfg):
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        v = win32com.client.Dispatch("SAPI.SpVoice")
        names = [t.GetDescription() for t in v.GetVoices()]
        hint = cfg["tts"]["voice_hint"]
        ko = [n for n in names if hint.lower() in n.lower()]
        return (OK, f"음성 안내: {ko[0]}") if ko else (FAIL, f"한국어 음성 없음 (설치된 음성: {names})")
    except Exception as e:
        return FAIL, f"음성 안내 사용 불가: {e}"


def check_mic(cfg, seconds: float = 1.5):
    try:
        import numpy as np
        import sounddevice as sd
        dev = cfg.get("voice_label", {}).get("device")
        rec = sd.rec(int(16000 * seconds), samplerate=16000, channels=1, dtype="int16", device=dev)
        sd.wait()
        rms = float(np.sqrt(np.mean(rec.astype(np.float32) ** 2)))
        name = sd.query_devices(dev if dev is not None else sd.default.device[0])["name"]
        if rms < 3:
            return WARN, f"마이크: 소리가 거의 없음 (rms {rms:.0f}, {name}). 음소거/권한 확인"
        return OK, f"마이크: 동작 (rms {rms:.0f}, {name})"
    except Exception as e:
        return WARN, f"마이크 사용 불가 (음성 라벨링 불가): {e}"


def check_camera(cfg):
    from csd.discover import find_camera, stream_url
    from csd.stream import iter_mjpeg
    import threading

    base = find_camera(cfg["camera"]["base_url"], allow_scan=cfg["camera"].get("scan_subnet", True))
    if not base:
        return FAIL, ("카메라를 찾지 못함. 카메라 전원 확인 -> 1분 기다린 뒤 노트북 Wi-Fi를 'CSD-CAM' "
                      "(비밀번호 csdcam1234)에 연결하고 다시 실행"), None
    stop = threading.Event()
    n, t0, size = 0, time.time(), 0
    try:
        for t, jpg in iter_mjpeg(stream_url(base), timeout=5, stop=stop):
            n += 1
            size += len(jpg)
            if time.time() - t0 > 3:
                break
    except Exception as e:
        return FAIL, f"카메라 {base} 발견, 스트림 실패: {e}", base
    fps = n / max(time.time() - t0, 1e-3)
    level = OK if fps >= 3 else WARN
    return level, f"카메라: {base}  스트림 {fps:.1f} fps, 프레임 평균 {size / max(n, 1) / 1000:.0f} KB", base


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-drive check")
    ap.add_argument("--config")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    cfg = cfgmod.load(a.config)

    results = [check_power(), check_disk(), check_models(cfg), check_voice(cfg), check_mic(cfg)]
    cam_level, cam_msg, _ = check_camera(cfg)
    results.append((cam_level, cam_msg))

    print("\n=== 출발 전 점검 ===")
    for level, text in results:
        print(f"[{level}] {text}")
    fails = sum(1 for lv, _ in results if lv == FAIL)
    warns = sum(1 for lv, _ in results if lv == WARN)
    summary = "점검 완료. 이상 없습니다" if not fails and not warns else \
        f"점검 완료. 실패 {fails}개, 주의 {warns}개가 있습니다. 화면을 확인하세요"
    print(f"\n{summary}\n")
    if not a.no_voice:
        try:
            import win32com.client
            v = win32com.client.Dispatch("SAPI.SpVoice")
            for t in v.GetVoices():
                if cfg["tts"]["voice_hint"].lower() in t.GetDescription().lower():
                    v.Voice = t
            v.Speak(summary)
        except Exception:
            pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
