"""Main pipeline: frame -> detect/track -> per-object analysers -> event log + voice."""

from __future__ import annotations

import argparse
import collections
import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from . import config as cfgmod
from . import messages as msg
from .camera_ctl import apply_settings
from .detect import Detection, Detector
from .ego_motion import EgoMotion
from .lanes import LaneDetector
from .risk import VEHICLE_CLASSES, RiskAnalyzer
from .state import EventLog, StateSmoother
from .stream import FileSource, MjpegStream
from .traffic_light import FlashTracker, classify_light
from .tts import INFO, AlertQueue, NullSpeaker, SapiSpeaker
from .vehicle_lights import VehicleLightTracker

log = logging.getLogger("csd")

EGO_ID = 0  # scene-level objects (lane, ego car, surroundings) use id 0


class _TrackState:
    """Everything remembered about one tracked object."""

    def __init__(self, cfg: dict):
        tl = cfg["traffic_light"]
        self.light = StateSmoother(tl["smooth_window_s"], tl["smooth_hold_s"])
        self.flash = FlashTracker()
        self.lamps: VehicleLightTracker | None = None
        self.lamp_smoother = StateSmoother(window_s=0.8, hold_s=0.3)
        self.motion_smoother = StateSmoother(window_s=1.2, hold_s=0.6)
        self.plate_voter = None
        self.plate_last_try = 0.0
        self.plate: str | None = None
        self.plate_future = None
        self.collision = "none"
        self.ttc: float | None = None
        self.last_pred: str | None = None  # latest per-frame light state (before smoothing)
        self.last_seen = 0.0


class Pipeline:
    def __init__(self, cfg: dict, speak: bool = True):
        self.cfg = cfg
        d = cfg["detector"]
        self.detector = Detector(str(cfgmod.resolve(d["model"])), d["imgsz"], d["conf"], d["device"],
                                 cfgmod.resolve(d["tracker"]))
        ln = cfg["lanes"]
        self.lanes = LaneDetector(ln["horizon_ratio"], ln["default_bottom_width"], ln["default_top_width"])
        rk = cfg["risk"]
        self.risk = RiskAnalyzer(ttc_warn=rk["ttc_warn"], ttc_danger=rk["ttc_danger"])
        self.ego = EgoMotion()
        self.ego_smoother = StateSmoother(window_s=1.5, hold_s=1.0)
        self.lane_smoother = StateSmoother(window_s=1.5, hold_s=1.0, min_share=0.7)
        self.events = EventLog(cfgmod.resolve(cfg["log_dir"]) if cfg.get("log_dir") else None)
        t = cfg["tts"]
        if speak and t["enabled"]:
            factory = lambda: SapiSpeaker(t["voice_hint"], t["rate"])  # noqa: E731
        else:
            factory = NullSpeaker
        self.voice = AlertQueue(factory, cooldown_s=t["cooldown_s"], max_age_s=t["max_age_s"]).start()
        self.plates = None
        self._plate_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plate")
        if cfg["plate"]["enabled"]:
            try:
                from .plate import PlateReader
                self.plates = PlateReader(cfgmod.resolve(cfg["plate"]["model_dir"]))
            except Exception as e:
                log.warning("plate reader disabled: %s", e)
        from .tl_classifier import load_if_present
        self.tl_cls = load_if_present(cfgmod.resolve(cfg["traffic_light"].get("classifier", "models/tl_cls.pt")))
        # Voice labelling (set by run() when --label-voice is used)
        self.labeler = None
        self.label_session = None
        self.tracks: dict[int, _TrackState] = {}
        self.lead_id: int | None = None
        self.relevant_light: int | None = None
        self.paths: dict[int, collections.deque] = {}
        self.last_dets: list[Detection] = []
        self.last_frame_w: int | None = None
        self.camera_link = ""  # e.g. "USB COM7" / "Wi-Fi http://192.168.4.1", shown on the monitor
        # Latest risk event for the monitor: (time, kind, text)
        self.last_risk: tuple[float, str, str] | None = None
        self.ego_state = "unknown"
        self._scene_last = 0.0
        self._scene_counts: dict[str, int] = {}
        self._scene_smoother = StateSmoother(window_s=2.0, hold_s=1.0)
        self.last_lane = None
        self.fps = 0.0

    # ----------------------------------------------------------------- helpers
    def _say(self, item, key: str, cooldown_s: float | None = None) -> None:
        if item:
            text, prio = item
            self.voice.say(text, prio, key=key, cooldown_s=cooldown_s)

    def _track(self, tid: int, t: float) -> _TrackState:
        ts = self.tracks.get(tid)
        if ts is None:
            ts = self.tracks[tid] = _TrackState(self.cfg)
        ts.last_seen = t
        return ts

    # --------------------------------------------------------------- per frame
    def process(self, frame: np.ndarray, t: float) -> list[Detection]:
        H, W = frame.shape[:2]
        self.last_frame_w = W
        dets = self.detector(frame)
        lane = self.last_lane = self.lanes.update(frame)

        # Ego motion (background flow with objects masked out)
        ego_raw = self.ego.update(frame, t, [d.box for d in dets])
        ego = self.ego_smoother.update(ego_raw if ego_raw != "unknown" else None, t)
        if ego:
            self.ego_state = ego
            self.events.emit(EGO_ID, "ego", ego)

        # Lane departure (only when both lane lines are really seen)
        lane_state = None
        if lane.detected:
            lane_state = f"departure_{lane.departure}" if lane.departure != "none" else "in_lane"
        confirmed_lane = self.lane_smoother.update(lane_state, t)
        if confirmed_lane:
            self.events.emit(EGO_ID, "lane", confirmed_lane)
            if confirmed_lane.startswith("departure_"):
                self.last_risk = (t, "lane", msg.lane_departure(confirmed_lane.split("_", 1)[1])[0])
                self._say(msg.lane_departure(confirmed_lane.split("_", 1)[1]), key=confirmed_lane)

        # Movement paths (bottom-centre of each box) for the monitor's trajectory trails
        for d in dets:
            if d.tid >= 0:
                path = self.paths.setdefault(d.tid, collections.deque(maxlen=40))
                path.append(((d.box[0] + d.box[2]) / 2, d.box[3], t))

        vehicles = {d.tid: d.box for d in dets if d.name in VEHICLE_CLASSES and d.tid >= 0}
        for tid, box in vehicles.items():
            self.risk.update_track(tid, box, t, lane.polygon, H)
        self.lead_id = self.risk.select_lead(vehicles, lane.polygon, H)

        relevant_light = self.relevant_light = self._relevant_light(dets)
        for d in dets:
            if d.tid < 0:
                continue
            ts = self._track(d.tid, t)
            if d.name == "traffic_light":
                self._traffic_light(d, ts, frame, t, speak=d.tid == relevant_light)
            elif d.name in VEHICLE_CLASSES:
                self._vehicle(d, ts, frame, t, W, is_lead=d.tid == self.lead_id)

        self._scene(dets, t)
        self._voice_labels(frame, dets, t)
        self._expire(t)
        self.last_dets = dets
        return dets

    def _voice_labels(self, frame, dets: list[Detection], t: float) -> None:
        if self.label_session is None:
            return
        light = next((d for d in dets if d.tid == self.relevant_light), None)
        ts = self.tracks.get(self.relevant_light) if light else None
        pred = (ts.light.confirmed or ts.last_pred) if ts else None
        self.label_session.add_frame(t, frame, light.box if light else None, light.tid if light else None, pred)
        if self.labeler is not None:
            while not self.labeler.labels.empty():
                self.label_session.submit(self.labeler.labels.get_nowait())
        for text in self.label_session.process(t):
            # Spoken confirmation so the driver never has to look at the screen.
            self.voice.say(text, INFO, key="voice_label", cooldown_s=0)

    def _relevant_light(self, dets: list[Detection]) -> int | None:
        """The traffic light that applies to us: the largest vehicle-signal head."""
        best, best_area = None, 0.0
        for d in dets:
            if d.name != "traffic_light" or d.tid < 0:
                continue
            x1, y1, x2, y2 = d.box
            area = (x2 - x1) * (y2 - y1)
            if area > best_area:
                best, best_area = d.tid, area
        return best

    def _traffic_light(self, d: Detection, ts: _TrackState, frame, t: float, speak: bool) -> None:
        x1, y1, x2, y2 = (int(v) for v in d.box)
        if min(x2 - x1, y2 - y1) < self.cfg["traffic_light"]["min_box_px"]:
            return
        # Learned classifier (if trained) first; rules as fallback for unsure predictions.
        raw, weight = None, 0.2
        if self.tl_cls is not None:
            raw, conf = self.tl_cls.predict(frame, d.box)
            weight = conf
        if raw is None:
            reading = classify_light(frame[y1:y2, x1:x2])
            if reading.state == "unknown":
                return
            raw, weight = reading.state, max(reading.confidence, 0.2)
        ts.last_pred = raw
        state = ts.flash.update(raw, t)
        confirmed = ts.light.update(state, t, weight=weight)
        if confirmed:
            self.events.emit(d.tid, d.name, confirmed)
            if speak:
                self._say(msg.traffic_light(confirmed), key="traffic_light", cooldown_s=1.0)

    def _vehicle(self, d: Detection, ts: _TrackState, frame, t: float, W: int, is_lead: bool) -> None:
        x1, y1, x2, y2 = d.box
        big_enough = (x2 - x1) >= W * self.cfg["vehicle_lights"]["min_box_w_ratio"]

        # Brake / turn signals: vehicles close enough to see their lamps
        if big_enough:
            ts.lamps = ts.lamps or VehicleLightTracker()
            ls = ts.lamps.update(frame, d.box, t)
            confirmed = ts.lamp_smoother.update(ls.label(), t)
            if confirmed:
                self.events.emit(d.tid, d.name, confirmed, channel="lamps")
                if is_lead:
                    if "brake_on" in confirmed:
                        self._say(msg.brake(True), key="lead_brake", cooldown_s=4.0)
                    for part in confirmed.split(",")[1:]:
                        self._say(msg.turn(part), key=f"lead_{part}", cooldown_s=6.0)

        # Cut-in: a vehicle moving from beside us into our lane
        side = self.risk.cut_in(d.tid, d.box, W, t)
        if side:
            self.events.emit(d.tid, d.name, side, force=True, channel="cut_in")
            self.last_risk = (t, "cut_in", f"{msg.cut_in(side)[0]} (ID {d.tid})")
            self._say(msg.cut_in(side), key=side, cooldown_s=4.0)

        if not is_lead:
            return

        # Collision risk from time-to-collision
        level, ttc = self.risk.collision_level(d.tid, d.box, W)
        ts.ttc = ttc
        state ="collision_clear" if level == "none" else f"collision_{level} ttc={ttc:.1f}s"
        if level != "none" or ts.collision != "none":
            # Log level changes only (the TTC shown is the one at the change).
            if level != ts.collision:
                self.events.emit(d.tid, d.name, state, force=True, channel="collision")
            ts.collision = level
        if level != "none":
            self.last_risk = (t, "collision", f"{msg.collision(level)[0]} TTC {ttc:.1f}s (ID {d.tid})")
            self._say(msg.collision(level), key=f"collision_{level}", cooldown_s=2.0)

        # Lead vehicle motion: stopped / starting / moving / slowing
        motion = self.risk.lead_state(d.tid, self.ego_state, t)
        confirmed = ts.motion_smoother.update(motion, t)
        if confirmed:
            self.events.emit(d.tid, d.name, f"lead_{confirmed}", channel="motion")
            self._say(msg.lead_motion(confirmed), key=f"lead_motion_{confirmed}", cooldown_s=10.0)

        # License plate: OCR runs on a background thread (the plate models take up to ~1 s on
        # this CPU), rate-limited, and stops once a plate is confirmed for the track.
        pc = self.cfg["plate"]
        if ts.plate_future is not None and ts.plate_future.done():
            try:
                text, _ = ts.plate_future.result()
            except Exception as e:
                log.debug("plate read failed: %s", e)
                text = None
            ts.plate_future = None
            confirmed_plate = ts.plate_voter.add(text)
            if confirmed_plate:
                ts.plate = confirmed_plate
                self.events.emit(d.tid, "license_plate", confirmed_plate)
                self._say(msg.plate(confirmed_plate), key=f"plate_{d.tid}", cooldown_s=60.0)
        if (self.plates and ts.plate is None and ts.plate_future is None
                and (x2 - x1) >= W * pc["min_box_w_ratio"] and t - ts.plate_last_try >= pc["every_s"]):
            ts.plate_last_try = t
            from .plate import PlateVoter
            ts.plate_voter = ts.plate_voter or PlateVoter(pc["min_votes"])
            crop = frame[int(max(0, y1)):int(y2), int(max(0, x1)):int(x2)].copy()
            ts.plate_future = self._plate_pool.submit(self.plates.read, crop)

    def _scene(self, dets: list[Detection], t: float) -> None:
        counts = {
            "vehicles": sum(1 for d in dets if d.name in VEHICLE_CLASSES - {"motorcycle"}),
            "person": sum(1 for d in dets if d.name == "person"),
            "two_wheeler": sum(1 for d in dets if d.name in ("motorcycle", "bicycle")),
        }
        # Counts flicker with detector confidence; log only a count held for ~1 s.
        confirmed = self._scene_smoother.update(",".join(f"{k}={v}" for k, v in counts.items()), t)
        if confirmed:
            self._scene_counts = dict(kv.split("=") for kv in confirmed.split(","))
            self._scene_counts = {k: int(v) for k, v in self._scene_counts.items()}
            self.events.emit(EGO_ID, "scene", confirmed)
        counts = self._scene_counts
        every = self.cfg["scene"]["announce_every_s"]
        if t - self._scene_last >= every:
            item = msg.scene(counts)
            if item:
                self._scene_last = t
                self._say((item[0], INFO), key="scene", cooldown_s=every)

    def _expire(self, t: float, ttl_s: float = 3.0) -> None:
        for tid in [k for k, v in self.tracks.items() if t - v.last_seen > ttl_s]:
            del self.tracks[tid]
            self.paths.pop(tid, None)
            self.events.forget(tid)
        self.risk.drop_missing(set(self.tracks))

    def close(self) -> None:
        self._plate_pool.shutdown(wait=False, cancel_futures=True)
        if self.labeler is not None:
            self.labeler.stop()
        if self.label_session is not None:
            self.label_session.close()
        self.voice.close()
        self.events.close()


def _wait_for_camera(cfg: dict, pipe: Pipeline, stop: dict, max_seconds: float | None) -> tuple[str, str] | None:
    """Find the camera, retrying (with a spoken hint) until found, stopped or timed out.

    Returns ("usb", serial_port) or ("wifi", base_url). transport "auto" prefers the USB
    cable (laptop Wi-Fi stays on the internet), then Wi-Fi.
    """
    from .discover import find_camera
    from .usb_camera import find_usb_camera

    cam = cfg["camera"]
    transport = cam.get("transport", "auto")
    t0, tries = time.time(), 0
    while not stop["flag"] and not (max_seconds and time.time() - t0 > max_seconds):
        if transport in ("auto", "usb"):
            port = find_usb_camera()
            if port:
                return "usb", port
        if transport in ("auto", "wifi"):
            base = find_camera(cam["base_url"], allow_scan=cam.get("scan_subnet", True))
            if base:
                return "wifi", base
        tries += 1
        log.warning("camera not found; retrying")
        pipe.voice.say("카메라를 찾는 중입니다. 카메라 USB 케이블을 확인하세요", INFO, key="cam_missing",
                       cooldown_s=20)
        time.sleep(3 if tries < 5 else 8)
    return None


def run(cfg: dict, source: str | None = None, max_seconds: float | None = None, speak: bool = True,
        show: bool | None = None, snapshot: str | None = None, realtime: bool = False,
        record: bool = False, label_voice: bool = False, name: str = "", fullscreen: bool = False) -> Pipeline:
    show = cfg.get("show", False) if show is None else show
    cam = cfg["camera"]
    pipe = Pipeline(cfg, speak=speak)
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))

    recorder = None
    if source:
        src = FileSource(source, realtime=realtime).start()
        session_dir = cfgmod.resolve(source) if cfgmod.resolve(source).is_dir() else None
    else:
        from .discover import stream_url
        found = (_wait_for_camera(cfg, pipe, stop, max_seconds) if cam.get("auto_discover", True)
                 else ("wifi", cam["base_url"]))
        if found is None:
            pipe.close()
            return pipe
        kind, where = found
        if record or label_voice:
            from .recorder import Recorder
            recorder = Recorder.new_session(cfgmod.resolve(cfg.get("record_dir", "recordings")), name)
            recorder.meta.update({"source": f"{kind}:{where}", "camera_settings": cam["settings"]})
            log.info("recording to %s", recorder.folder)
        on_jpeg = recorder.add if recorder else None
        if kind == "usb":
            from .usb_camera import UsbCamStream
            src = UsbCamStream(where, on_jpeg=on_jpeg).start()
            if cam.get("apply_settings"):
                src.apply_settings(cam["settings"])
            log.info("camera: USB %s", where)
        else:
            cfg["camera"]["base_url"] = where
            if cam.get("apply_settings"):
                status = apply_settings(where, cam["settings"])
                log.info("camera %s: framesize=%s quality=%s", where, status.get("framesize"), status.get("quality"))
            src = MjpegStream(stream_url(where), on_jpeg=on_jpeg).start()
        pipe.camera_link = f"{'USB' if kind == 'usb' else 'Wi-Fi'} {where}"
        session_dir = recorder.folder if recorder else None

    if label_voice:
        from .labeling import LabelSession
        from .voice_label import VoiceLabeler
        if session_dir is None:
            session_dir = cfgmod.resolve("recordings") / time.strftime("%Y%m%d_%H%M%S_labels")
        pipe.label_session = LabelSession(session_dir)
        vl = cfg.get("voice_label", {})
        pipe.labeler = VoiceLabeler(cfgmod.resolve(vl.get("model", "models/vosk-model-small-ko-0.22")),
                                    is_muted=pipe.voice.speaking_recently, device=vl.get("device")).start()

    start_msg = "차량 신호 감지를 시작합니다"
    if label_voice:
        start_msg += ". 신호등 상태를 말씀하시면 학습 데이터로 저장합니다"
    pipe.voice.say(start_msg, INFO, key="start")

    dashboard = None
    if show or snapshot:
        from .dashboard import Dashboard
        dashboard = Dashboard()
        if show:
            cv2.namedWindow("car_siginal_detector", cv2.WINDOW_NORMAL)
            if fullscreen:
                cv2.setWindowProperty("car_siginal_detector", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    seq, t_start, n = -1, time.time(), 0
    screen = None
    try:
        while not stop["flag"]:
            if max_seconds and time.time() - t_start > max_seconds:
                break
            seq, t, frame = src.read(seq)
            if frame is None:
                if source:
                    break
                continue
            t0 = time.time()
            dets = pipe.process(frame, t)
            n += 1
            pipe.fps = 0.9 * pipe.fps + 0.1 / max(time.time() - t0, 1e-3) if n > 1 else 1 / max(time.time() - t0, 1e-3)
            if dashboard is not None:
                screen = dashboard.render(frame, dets, pipe)
            if show:
                cv2.imshow("car_siginal_detector", screen)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    finally:
        if snapshot and screen is not None:
            from .labeling import imwrite_any
            from pathlib import Path
            imwrite_any(Path(snapshot), screen)
            log.info("dashboard snapshot saved: %s", snapshot)
        src.stop()
        if recorder is not None:
            meta = recorder.close(labels=dict(pipe.label_session.counts) if pipe.label_session else {})
            log.info("recording saved: %s (%d frames, %.0f s)", recorder.folder, meta["frames"], meta["duration_s"])
        if pipe.label_session is not None:
            log.info("voice labels this session: %s", dict(pipe.label_session.counts) or "none")
        pipe.close()
        if show:
            cv2.destroyAllWindows()
        log.info("processed %d frames in %.1fs", n, time.time() - t_start)
    return pipe


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m csd", description="ESP32-CAM traffic signal & vehicle state detector")
    ap.add_argument("--config", help="YAML file overriding configs/default.yaml")
    ap.add_argument("--source", help="recording folder (tools/record.py), image folder or video file")
    ap.add_argument("--realtime", action="store_true",
                    help="with --source: replay at recorded speed and skip late frames, like live")
    ap.add_argument("--show", action="store_true", help="open the driving-situation monitor window (q/Esc to quit)")
    ap.add_argument("--snapshot", help="save the last monitor screen to this PNG path on exit")
    ap.add_argument("--fullscreen", action="store_true", help="with --show: full-screen monitor window")
    ap.add_argument("--no-voice", action="store_true", help="disable voice alerts")
    ap.add_argument("--record", action="store_true", help="save the camera stream to recordings/<time>")
    ap.add_argument("--label-voice", action="store_true",
                    help="hands-free training labels: say the light state (빨간불, 좌회전, ...); implies --record")
    ap.add_argument("--name", default="", help="label appended to the recording folder name")
    ap.add_argument("--seconds", type=float, help="stop after N seconds")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = cfgmod.load(a.config)
    run(cfg, a.source, a.seconds, speak=not a.no_voice, show=a.show or None, snapshot=a.snapshot,
        realtime=a.realtime, record=a.record, label_voice=a.label_voice, name=a.name, fullscreen=a.fullscreen)
