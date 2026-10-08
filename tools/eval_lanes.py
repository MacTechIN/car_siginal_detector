"""Compare classical lane finding with the learned model (TwinLiteNet) on a recording.

The model's lines serve as the reference ("learned data"). For evenly spaced frames:
  * detection rate of both ego-lane lines, classical vs model,
  * when both found them: offset of the classical lines from the model's at the bonnet
    line (% of image width),
  * paint colour and solid/dashed of the model lines, run time of each method,
and a side-by-side contact sheet (classical | model) to look at.

  python tools/eval_lanes.py recordings/20261006_173343_app [--frames 40] [--out scratch/lanes]
"""

from __future__ import annotations

import argparse
import collections
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from csd import config as cfgmod  # noqa: E402
from csd.lane_model import TwinLiteNet, extract_lanes  # noqa: E402
from csd.lanes import LaneDetector  # noqa: E402
from csd.stream import imread_any, load_timestamps  # noqa: E402


def save(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tofile(str(path))  # unicode-safe


def draw(img, left, right, color, hood_y=None):
    v = img.copy()
    H = v.shape[0]
    for ln in (left, right):
        if ln is None:
            continue
        if hasattr(ln, "points"):
            cv2.polylines(v, [ln.points(32)], False, color, 4, cv2.LINE_AA)
        else:
            a, b = ln
            y0, y1 = int(H * 0.55), int(hood_y or H)
            cv2.line(v, (int(a * y0 + b), y0), (int(a * y1 + b), y1), color, 4, cv2.LINE_AA)
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--input", default="320x192", help="model input WxH")
    ap.add_argument("--out", default=str(ROOT / "scratch" / "lanes"))
    a = ap.parse_args()

    cfg = cfgmod.load()
    ln = cfg["lanes"]
    rec = Path(a.recording)
    files = [f for f, _ in (load_timestamps(rec) or [])] or sorted(p.name for p in rec.glob("*.jpg"))
    idx = np.linspace(0, len(files) - 1, a.frames).astype(int)
    net = TwinLiteNet(cfgmod.resolve(ln["model"]), tuple(int(v) for v in a.input.split("x")), threads=4,
                      device=ln.get("model_device", "GPU"), cache_dir=cfgmod.resolve("models/ov_cache"))

    stats = collections.Counter()
    offs, t_classic, t_model, kinds = [], [], [], collections.Counter()
    tiles = []
    for n, i in enumerate(idx):
        img = imread_any(rec / files[i])
        if img is None:
            continue
        W = img.shape[1]
        # Classical: a fresh detector warmed up on the 5 frames before (it smooths over time)
        cl = LaneDetector(ln["horizon_ratio"], ln["default_bottom_width"], ln["default_top_width"])
        for j in range(max(0, i - 5), i):
            prev = imread_any(rec / files[j])
            if prev is not None:
                cl.update(prev)
        t0 = time.time()
        rc = cl.update(img)
        t_classic.append(time.time() - t0)
        t0 = time.time()
        da, ll = net.masks(img)
        rm = extract_lanes(da, ll, img, 0.0)
        t_model.append(time.time() - t0)

        m_both = rm.left is not None and rm.right is not None
        stats["frames"] += 1
        stats["classic_both"] += rc.detected
        stats["model_both"] += m_both
        stats["model_one"] += (rm.left is not None) + (rm.right is not None) == 1
        for line in (rm.left, rm.right):
            if line is not None:
                kinds[line.kind] += 1
        if rc.detected and m_both:
            y = rm.hood_y or img.shape[0]
            for c_line, m_line in ((rc.left, rm.left), (rc.right, rm.right)):
                offs.append(abs((c_line[0] * y + c_line[1]) - float(m_line.x_at(y))) / W * 100)
        left_c = draw(img, rc.left, rc.right, (0, 0, 255), rm.hood_y)
        right_m = draw(img, rm.left, rm.right, (0, 255, 0))
        cv2.putText(left_c, f"classic {'OK' if rc.detected else '-'}", (10, 40), 0, 1.2, (0, 0, 255), 3)
        cv2.putText(right_m, f"model {'OK' if m_both else '-'} " +
                    " / ".join(x.kind for x in (rm.left, rm.right) if x is not None), (10, 40), 0, 1.0, (0, 255, 0), 3)
        tiles.append(np.hstack([cv2.resize(left_c, (480, 360)), cv2.resize(right_m, (480, 360))]))
        print(f"{n + 1}/{len(idx)} {files[i]} classic={rc.detected} model={m_both}", flush=True)

    out = Path(a.out)
    for k in range(0, len(tiles), 6):
        save(out / f"{rec.name}_{k // 6:02d}.jpg", np.vstack(tiles[k:k + 6]))
    f = max(1, stats["frames"])
    print(f"\n{rec.name}: {stats['frames']} frames")
    print(f"  both lane lines found   classic {stats['classic_both'] / f:.0%}   model {stats['model_both'] / f:.0%}"
          f"   (model one side only {stats['model_one'] / f:.0%})")
    if offs:
        print(f"  classic vs model at the bonnet line: median {np.median(offs):.1f}% of width, "
              f"90th pct {np.percentile(offs, 90):.1f}% ({len(offs)} lines)")
    print(f"  model line kinds: {dict(kinds)}")
    print(f"  time per frame: classic {np.median(t_classic) * 1000:.0f} ms, model {np.median(t_model) * 1000:.0f} ms")
    print(f"  sheets: {out}")


if __name__ == "__main__":
    main()
