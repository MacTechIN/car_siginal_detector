"""Object detection + multi-object tracking (Ultralytics YOLO + ByteTrack).

A COCO-pretrained YOLO already covers the classes this app needs: car, truck, bus,
motorcycle, bicycle, person and traffic light. Tracking gives each object a stable id
so states can be smoothed per object and logged as `id : [name] : "state"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

WANTED = {"car", "truck", "bus", "motorcycle", "bicycle", "person", "traffic light"}
# Names used in the log (spec uses snake_case, e.g. traffic_light)
LOG_NAME = {"traffic light": "traffic_light"}


@dataclass
class Detection:
    tid: int
    name: str        # log name, e.g. traffic_light, car
    box: tuple[float, float, float, float]
    conf: float


class Detector:
    def __init__(self, model: str, imgsz: int = 640, conf: float = 0.25, device: str = "cpu",
                 tracker: str | Path = "configs/tracker.yaml"):
        from ultralytics import YOLO

        self.model = YOLO(model, task="detect")
        self.imgsz = imgsz
        self.conf = conf
        self.device = device
        self.tracker = str(tracker)
        names = self.model.names
        self.class_ids = [i for i, n in names.items() if n in WANTED]
        self._untracked = -1

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        r = self.model.track(frame, imgsz=self.imgsz, conf=self.conf, classes=self.class_ids,
                             persist=True, tracker=self.tracker, device=self.device, verbose=False)[0]
        out: list[Detection] = []
        b = r.boxes
        if b is None or len(b) == 0:
            return out
        ids = b.id.int().tolist() if b.id is not None else [None] * len(b)
        for tid, cls, conf, xyxy in zip(ids, b.cls.int().tolist(), b.conf.tolist(), b.xyxy.tolist()):
            name = r.names[cls]
            if tid is None:
                # Not yet confirmed by the tracker: give a temporary negative id (not logged).
                tid = self._untracked
                self._untracked -= 1
            out.append(Detection(int(tid), LOG_NAME.get(name, name), tuple(xyxy), float(conf)))
        return out
