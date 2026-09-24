"""Download model weights into models/.

  models/yolo26s.pt (and n/m on request)   Ultralytics COCO detector (AGPL-3.0)
  models/plates/*.pt                       sauce-hug/korean-license-plate-detector (Apache-2.0 code repo)

Usage: python tools/fetch_models.py [--detector yolo26s yolo26n]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
PLATE_REPO = "https://huggingface.co/sauce-hug/korean-license-plate-detector/resolve/main"
PLATE_FILES = ["plate_detect_v1", "vertex_detect_v1", "syllable_detect_v1"]
VOSK_NAME = "vosk-model-small-ko-0.22"


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"ok      {dest.relative_to(ROOT)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    print(f"fetched {dest.relative_to(ROOT)} ({dest.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", nargs="*", default=["yolo26s"])
    a = ap.parse_args()

    from ultralytics.utils.downloads import attempt_download_asset

    MODELS.mkdir(exist_ok=True)
    for name in a.detector:
        dest = MODELS / f"{name}.pt"
        if not dest.exists():
            path = Path(attempt_download_asset(f"{name}.pt"))
            shutil.move(str(path), dest)
        print(f"ok      {dest.relative_to(ROOT)}")
    for name in PLATE_FILES:
        download(f"{PLATE_REPO}/{name}/weights/best.pt", MODELS / "plates" / f"{name}.pt")

    # Offline Korean speech recognition for voice labelling (Apache-2.0, alphacephei.com/vosk)
    vosk_dir = MODELS / VOSK_NAME
    if not vosk_dir.exists():
        zip_path = MODELS / f"{VOSK_NAME}.zip"
        download(f"https://alphacephei.com/vosk/models/{VOSK_NAME}.zip", zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(MODELS)
        zip_path.unlink()
    print(f"ok      {vosk_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
