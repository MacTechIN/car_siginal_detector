"""Learned Korean traffic-light state classifier (optional).

Trained by tools/train_tl.py on voice-labelled crops. When models/tl_cls.pt exists the
pipeline uses it and falls back to the rule-based `classify_light` for low-confidence
predictions. Input crops are prepared exactly as in training (labeling.square_crop).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .labeling import square_crop

log = logging.getLogger(__name__)


class TLClassifier:
    def __init__(self, model_path: str | Path, imgsz: int = 96, min_conf: float = 0.6):
        from ultralytics import YOLO

        self.model = YOLO(str(model_path), task="classify")
        self.imgsz = imgsz
        self.min_conf = min_conf
        self.names = self.model.names
        log.info("traffic-light classifier: %s classes=%s", model_path, list(self.names.values()))

    def predict(self, frame: np.ndarray, box) -> tuple[str | None, float]:
        crop = square_crop(frame, box, size=self.imgsz)
        if crop is None:
            return None, 0.0
        r = self.model.predict(crop, imgsz=self.imgsz, verbose=False)[0]
        i, conf = int(r.probs.top1), float(r.probs.top1conf)
        return (self.names[i], conf) if conf >= self.min_conf else (None, conf)


def load_if_present(path: str | Path, **kw) -> TLClassifier | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return TLClassifier(p, **kw)
    except Exception as e:
        log.warning("traffic-light classifier not loaded (%s); using rules", e)
        return None
