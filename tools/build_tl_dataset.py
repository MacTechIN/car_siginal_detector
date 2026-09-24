"""Collect voice-labelled traffic-light crops into a classification dataset.

Sources
  recordings/*/crops/<class>/*.jpg     crops saved while driving (python -m csd --label-voice)
  data/extra_tl/<class>/*.jpg          any extra crops you add by hand (e.g. from AI Hub)

Output (ImageFolder layout for Ultralytics classification):
  dataset/tl_cls/train/<class>/*.jpg
  dataset/tl_cls/val/<class>/*.jpg

Crops from one label event (same session, same second) are highly similar, so the
train/val split is done per event, never per image, to keep validation honest.

Usage: python tools/build_tl_dataset.py [--val 0.2] [--min-per-class 20]
"""

from __future__ import annotations

import argparse
import collections
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG_EXT = {".jpg", ".jpeg", ".png"}


def collect(roots: list[Path]) -> dict[str, dict[str, list[Path]]]:
    """class -> event key -> files."""
    data: dict[str, dict[str, list[Path]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for root in roots:
        for crops in [root] if root.name == "extra_tl" else sorted(root.glob("*/crops")):
            session = crops.parent.name
            for cls_dir in sorted(p for p in crops.iterdir() if p.is_dir()):
                for f in sorted(cls_dir.iterdir()):
                    if f.suffix.lower() in IMG_EXT:
                        # file name = <event_ms>_<track>_<k>.jpg -> group by label event
                        event = f"{session}/{f.stem.split('_')[0]}"
                        data[cls_dir.name][event].append(f)
    return data


def build(recordings: Path, extra: Path, out: Path, val: float, seed: int = 0) -> dict:
    data = collect([p for p in (recordings, extra) if p.exists()])
    if out.exists():
        shutil.rmtree(out)
    rnd = random.Random(seed)
    summary = {}
    for cls, events in sorted(data.items()):
        keys = sorted(events)
        rnd.shuffle(keys)
        n_val = max(1, round(len(keys) * val)) if len(keys) > 1 else 0
        split = {"val": keys[:n_val], "train": keys[n_val:]}
        counts = {}
        for part, ks in split.items():
            d = out / part / cls
            d.mkdir(parents=True, exist_ok=True)
            n = 0
            for k in ks:
                for f in events[k]:
                    shutil.copy2(f, d / f"{k.replace('/', '_')}_{f.name}")
                    n += 1
            counts[part] = n
        summary[cls] = {"events": len(keys), **counts}
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recordings", default=str(ROOT / "recordings"))
    ap.add_argument("--extra", default=str(ROOT / "data" / "extra_tl"))
    ap.add_argument("--out", default=str(ROOT / "dataset" / "tl_cls"))
    ap.add_argument("--val", type=float, default=0.2)
    ap.add_argument("--min-per-class", type=int, default=20)
    a = ap.parse_args()

    summary = build(Path(a.recordings), Path(a.extra), Path(a.out), a.val)
    if not summary:
        print("no labelled crops found. Record with: python -m csd --label-voice")
        return 1
    print(f"{'class':16s} {'events':>6s} {'train':>6s} {'val':>5s}")
    for cls, s in summary.items():
        warn = "  <- too few, collect more" if s.get("train", 0) < a.min_per_class else ""
        print(f"{cls:16s} {s['events']:6d} {s.get('train', 0):6d} {s.get('val', 0):5d}{warn}")
    print(f"dataset -> {a.out}")
    if len(summary) < 2:
        print("need at least 2 classes to train a classifier")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
