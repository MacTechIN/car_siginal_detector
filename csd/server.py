"""Local HTTP interface for the Windows app (windows_app/): the engine runs without its own
window and the native app shows video and state.

  GET  /health      {"ok": true}
  GET  /frame.jpg   latest camera view with overlays (JPEG); header X-Frame-Seq
  GET  /state       everything the app displays, Korean text included (JSON)
  POST /shutdown    stop the engine cleanly (recording and labels are finalised)

Bound to 127.0.0.1 only: nothing is reachable from the network.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

from .dashboard import (EGO_KO, LANE_KO, LIGHT_KO, MOTION_KO, NAME_KO, describe, lamps_ko)
from .overlay import draw as draw_overlay

log = logging.getLogger(__name__)

PRIORITY_NAME = {0: "danger", 1: "warning", 2: "signal", 3: "info"}


def build_state(pipe, now: float | None = None) -> dict:
    """Snapshot of the pipeline for the app (plain JSON types only)."""
    now = time.time() if now is None else now
    tracks = pipe.tracks

    light = None
    if pipe.relevant_light is not None and pipe.relevant_light in tracks:
        st = tracks[pipe.relevant_light].light.confirmed
        light = {"id": pipe.relevant_light, "state": st, "text": LIGHT_KO.get(st, "판단 중") if st else "판단 중"}

    lead = None
    if pipe.lead_id is not None and pipe.lead_id in tracks:
        ts = tracks[pipe.lead_id]
        lamps = ts.lamp_smoother.confirmed or ""
        motion = ts.motion_smoother.confirmed
        lead = {
            "id": pipe.lead_id, "motion": motion, "motion_text": MOTION_KO.get(motion or "", "판단 중"),
            "plate": ts.plate, "brake": "brake_on" in lamps,
            "left": "left_turn" in lamps, "right": "right_turn" in lamps, "hazard": "hazard" in lamps,
            "lamps_text": lamps_ko(lamps), "collision": ts.collision,
            "ttc": round(ts.ttc, 2) if ts.ttc else None,
        }

    risk_level = lead["collision"] if lead else "none"
    recent = pipe.last_risk if pipe.last_risk and now - pipe.last_risk[0] < 6.0 else None
    if risk_level == "none" and recent and recent[1] in ("cut_in", "lane"):
        risk_level = "warning"

    caption = None
    spoken = getattr(pipe.voice, "spoken", None)
    if spoken:
        t, text, prio = spoken[-1]
        caption = {"text": text, "priority": PRIORITY_NAME.get(prio, "info"), "age_s": round(now - t, 1)}

    objects = []
    for det in sorted((d for d in pipe.last_dets if d.tid >= 0),
                      key=lambda d: (d.tid != pipe.lead_id, d.name != "traffic_light", d.tid)):
        text, plate = describe(det, tracks.get(det.tid))
        objects.append({"id": det.tid, "name": det.name, "name_text": NAME_KO.get(det.name, det.name),
                        "state_text": text, "plate": plate, "lead": det.tid == pipe.lead_id})

    lane = pipe.lane_smoother.confirmed
    lane_frac = None
    if pipe.last_lane is not None and pipe.last_frame_w:
        (lbx, _), _, _, (rbx, _) = pipe.last_lane.polygon
        if rbx > lbx:
            lane_frac = round(min(max((pipe.last_frame_w / 2 - lbx) / (rbx - lbx), 0.0), 1.0), 3)

    labeler, session = getattr(pipe, "labeler", None), getattr(pipe, "label_session", None)
    labeling = None
    if session is not None:
        labeling = {
            "listening": bool(labeler and labeler.listening), "error": labeler.error if labeler else None,
            "heard": labeler.heard_log[-1][1] if labeler and labeler.heard_log else None,
            "counts": {k: v for k, v in session.counts.items() if v > 0},
        }

    return {
        "phase": "running", "phase_text": "동작 중",
        "time": now, "fps": round(pipe.fps, 1), "camera": getattr(pipe, "camera_link", ""),
        "frame_seq": getattr(pipe, "frame_seq", 0),
        "ego": pipe.ego_state, "ego_text": EGO_KO.get(pipe.ego_state, pipe.ego_state),
        "lane": lane, "lane_text": LANE_KO.get(lane, "차선 미검출") if lane else "차선 미검출",
        "lane_detected": bool(pipe.last_lane is not None and pipe.last_lane.detected), "lane_pos": lane_frac,
        "light": light, "lead": lead,
        "risk": {"level": risk_level, "text": recent[2] if recent else None,
                 "age_s": round(now - recent[0], 1) if recent else None},
        "scene": dict(pipe._scene_counts or {}), "caption": caption, "objects": objects,
        "events": [{"t": ev.t, "line": ev.line()} for ev in pipe.events.history[-12:]][::-1],
        "labeling": labeling,
    }


class EngineServer:
    """Serves the latest frame/state; the pipeline thread calls publish() after each frame."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, jpeg_quality: int = 80):
        self.host, self.port = host, port
        self.jpeg_quality = jpeg_quality
        self.shutdown_requested = threading.Event()
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._state: dict = {"starting": True}
        self._httpd: ThreadingHTTPServer | None = None

    def publish(self, frame, dets, pipe) -> None:
        view = draw_overlay(frame, dets, pipe)
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        with self._lock:
            if ok:
                self._jpeg = buf.tobytes()
                self._seq += 1
            pipe.frame_seq = self._seq
            self._state = build_state(pipe)

    def set_status(self, **fields) -> None:
        """State before the first frame (e.g. searching for the camera)."""
        with self._lock:
            self._state = {**self._state, **fields}

    def start(self) -> "EngineServer":
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, str(v))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/health":
                    self._send(200, b'{"ok":true}', "application/json")
                elif path == "/state":
                    with server._lock:
                        body = json.dumps(server._state, ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                elif path == "/frame.jpg":
                    with server._lock:
                        jpg, seq = server._jpeg, server._seq
                    if jpg is None:
                        self._send(204, b"", "image/jpeg")
                    else:
                        self._send(200, jpg, "image/jpeg", {"X-Frame-Seq": seq})
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if self.path == "/shutdown":
                    server.shutdown_requested.set()
                    self._send(200, b'{"ok":true}', "application/json")
                else:
                    self._send(404, b"not found", "text/plain")

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, name="engine-http", daemon=True).start()
        log.info("engine server on http://%s:%d", self.host, self.port)
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
