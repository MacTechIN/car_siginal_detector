"""Download model weights into models/.

  models/yolo26s.pt (and n/m on request)   Ultralytics COCO detector (AGPL-3.0)
  models/plates/*.pt                       sauce-hug/korean-license-plate-detector (Apache-2.0 code repo)
  models/twinlitenet.onnx                  chequanghuy/TwinLiteNet lane lines + drivable area (MIT),
                                           exported from pretrained/best.pth

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
TWINLITE_ZIP = "https://github.com/chequanghuy/TwinLiteNet/archive/refs/heads/main.zip"


def export_twinlitenet(dest: Path) -> None:
    """Download TwinLiteNet (code + pretrained/best.pth) and export it to ONNX with a free
    input size (the network is fully convolutional)."""
    if dest.exists():
        print(f"ok      {dest.relative_to(ROOT)}")
        return
    import tempfile

    import torch

    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        zip_path = Path(tmp) / "twinlitenet.zip"
        download(TWINLITE_ZIP, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        repo = Path(tmp) / "TwinLiteNet-main"
        sys.path.insert(0, str(repo))
        from model import TwinLite  # noqa: E402  (the repo's model definition)

        net = TwinLite.TwinLiteNet()
        state = torch.load(repo / "pretrained" / "best.pth", map_location="cpu")
        net.load_state_dict({k.replace("module.", ""): v for k, v in state.items()})
        net.eval()
        axes = {2: "h", 3: "w"}
        torch.onnx.export(net, torch.rand(1, 3, 360, 640), str(dest), input_names=["img"],
                          output_names=["da", "ll"], opset_version=17, dynamo=False,
                          dynamic_axes={"img": axes, "da": axes, "ll": axes})
        sys.path.remove(str(repo))
    print(f"ok      {dest.relative_to(ROOT)} (exported)")


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

    export_twinlitenet(MODELS / "twinlitenet.onnx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
