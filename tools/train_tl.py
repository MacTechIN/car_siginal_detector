"""Train the Korean traffic-light state classifier on dataset/tl_cls.

Fine-tunes Ultralytics YOLO26n-cls (ImageNet-pretrained, 5.5 MB) on the voice-labelled
crops, then installs the best weights as models/tl_cls.pt, which the app loads
automatically (see csd/tl_classifier.py). CPU training of a few hundred 96x96 crops
takes minutes.

Usage:
  python tools/build_tl_dataset.py
  python tools/train_tl.py [--epochs 30] [--imgsz 96]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Train the traffic-light state classifier")
    ap.add_argument("--data", default=str(ROOT / "dataset" / "tl_cls"))
    ap.add_argument("--base", default=str(ROOT / "models" / "yolo26n-cls.pt"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--imgsz", type=int, default=96)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--out", default=str(ROOT / "models" / "tl_cls.pt"))
    a = ap.parse_args()

    data = Path(a.data)
    if not (data / "train").exists():
        print(f"no dataset at {data}; run tools/build_tl_dataset.py first")
        return 1

    from ultralytics import YOLO

    base = a.base if Path(a.base).exists() else "yolo26n-cls.pt"
    model = YOLO(base, task="classify")
    run_name = time.strftime("tl_cls_%Y%m%d_%H%M%S")
    model.train(
        data=str(data), epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device="cpu", workers=0,
        project=str(ROOT / "runs"), name=run_name, exist_ok=True, patience=10, verbose=False,
        # Colour is the signal: no hue jitter or flips (left arrow != right arrow).
        hsv_h=0.0, hsv_s=0.2, hsv_v=0.3, fliplr=0.0, flipud=0.0, erasing=0.0, auto_augment=None,
        # Random crops keep 90-100 % of the area (default 50-100 % can cut off the red lamp).
        scale=0.1,
    )
    best = ROOT / "runs" / run_name / "weights" / "best.pt"
    if not best.exists():
        print("training finished without best.pt")
        return 1
    metrics = YOLO(str(best), task="classify").val(data=str(data), imgsz=a.imgsz, device="cpu", verbose=False)
    out = Path(a.out)
    if out.exists():
        shutil.copy2(out, out.with_name(out.stem + "_prev.pt"))
    shutil.copy2(best, out)
    info = {"trained": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": run_name, "imgsz": a.imgsz, "epochs": a.epochs,
            "top1": round(float(metrics.top1), 4), "classes": list(YOLO(str(out), task="classify").names.values())}
    out.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"val top1 accuracy {info['top1']:.3f} on {len(info['classes'])} classes -> installed {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
