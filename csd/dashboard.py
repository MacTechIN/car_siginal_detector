"""Driving-situation monitor (console window).

  ┌──────────────────────── 음성 안내 자막 (큰 글씨, 위험도별 색) ────────────────────────┐
  ├──────────────── 실시간 영상 ─────────────────┬──────────── 상황판 ─────────────┤
  │ 자차 차선 영역 + 진행 방향 화살표               │ 신호등 상태 (램프 + 큰 글씨)     │
  │ 객체 박스/ID, 앞차(LEAD) 강조                   │ 위험 감지 (안전/주의/위험, TTC)  │
  │ 차량별 이동 경로 궤적                           │ 앞차: 번호판, 주행 상태, 등화    │
  │                                               │ 현재 차선 (차선 안 자차 위치)    │
  │                                               │ 분석 결과 표 (ID/객체/상태)      │
  ├───────────────────────────────────────────────┴─────────────────────────────────┤
  │ 이벤트 로그  id : [이름] : "상태"                          fps · 시각 · 자차 상태  │
  └──────────────────────────────────────────────────────────────────────────────────┘
OpenCV cannot draw Hangul, so everything except the camera overlays is drawn with Pillow
using Malgun Gothic.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .overlay import draw as draw_overlay
from .tts import DANGER, INFO, SIGNAL, WARNING

WIN_W, BANNER_H, VIEW_W, VIEW_H, LOG_H = 1440, 110, 880, 660, 140
PANEL_W = WIN_W - VIEW_W
WIN_H = BANNER_H + VIEW_H + LOG_H

BG = (20, 22, 26)
CARD = (34, 37, 43)
TEXT = (235, 237, 240)
DIM = (135, 142, 152)
RED = (235, 65, 55)
YELLOW = (245, 190, 40)
GREEN = (55, 200, 105)
BLUE = (70, 150, 245)
ORANGE = (245, 135, 35)
OFF = (62, 66, 73)

CAPTION_COLORS = {DANGER: (200, 30, 30), WARNING: (215, 110, 20), SIGNAL: (35, 95, 190), INFO: (55, 60, 70)}
CAPTION_TTL_S = 5.0

LIGHT_KO = {"red": "빨간불", "yellow": "노란불", "green": "초록불", "left": "좌회전", "green_left": "직진·좌회전",
            "red_left": "좌회전 (직진 정지)", "red_yellow": "신호 변경 중", "flashing_yellow": "황색 점멸",
            "flashing_red": "적색 점멸", "off": "꺼짐"}
LIGHT_COLOR = {"red": RED, "red_left": RED, "flashing_red": RED, "yellow": YELLOW, "flashing_yellow": YELLOW,
               "red_yellow": YELLOW, "green": GREEN, "left": GREEN, "green_left": GREEN}
LAMPS = {"red": (1, 0, 0, 0), "yellow": (0, 1, 0, 0), "green": (0, 0, 0, 1), "left": (0, 0, 1, 0),
         "green_left": (0, 0, 1, 1), "red_left": (1, 0, 1, 0), "red_yellow": (1, 1, 0, 0),
         "flashing_yellow": (0, 1, 0, 0), "flashing_red": (1, 0, 0, 0)}
MOTION_KO = {"stopped": "정지", "starting": "출발", "moving": "주행", "slowing": "감속"}
LANE_KO = {"in_lane": "차선 유지 중", "departure_left": "왼쪽 차선 이탈!", "departure_right": "오른쪽 차선 이탈!"}
EGO_KO = {"stopped": "정차", "moving": "주행", "unknown": "판단 중"}
NAME_KO = {"car": "승용차", "truck": "트럭", "bus": "버스", "motorcycle": "오토바이", "bicycle": "자전거",
           "person": "보행자", "traffic_light": "신호등"}


def _font(size: int, bold: bool = False):
    for name in (("malgunbd.ttf",) if bold else ()) + ("malgun.ttf",):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def lamps_ko(label: str | None) -> str:
    if not label:
        return ""
    parts = []
    if "brake_on" in label:
        parts.append("브레이크")
    for key, ko in (("left_turn", "좌측 깜빡이"), ("right_turn", "우측 깜빡이"), ("hazard", "비상등")):
        if key in label:
            parts.append(ko)
    return " · ".join(parts)


class Dashboard:
    def __init__(self):
        self.f_caption = _font(46, bold=True)
        self.f_huge = _font(34, bold=True)
        self.f_big = _font(26, bold=True)
        self.f_mid = _font(19, bold=True)
        self.f = _font(17)
        self.f_small = _font(14)
        self.f_log = _font(15)

    # ------------------------------------------------------------------ public
    def render(self, frame: np.ndarray | None, dets, pipe) -> np.ndarray:
        img = Image.new("RGB", (WIN_W, WIN_H), BG)
        d = ImageDraw.Draw(img)
        now = time.time()
        self._banner(d, pipe, now)
        if frame is not None:
            view = draw_overlay(frame, dets, pipe)
            s = min(VIEW_W / view.shape[1], VIEW_H / view.shape[0])
            view = cv2.resize(view, (int(view.shape[1] * s), int(view.shape[0] * s)))
            vx = (VIEW_W - view.shape[1]) // 2
            vy = BANNER_H + (VIEW_H - view.shape[0]) // 2
            img.paste(Image.fromarray(cv2.cvtColor(view, cv2.COLOR_BGR2RGB)), (vx, vy))
        self._panel(d, pipe, now)
        self._log(d, pipe)
        out = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        lead = pipe.tracks.get(pipe.lead_id) if pipe.lead_id is not None else None
        if lead is not None and lead.collision == "danger" and int(now * 4) % 2 == 0:
            cv2.rectangle(out, (0, 0), (WIN_W - 1, WIN_H - 1), (0, 0, 255), 14)
        return out

    # ---------------------------------------------------------------- sections
    def _banner(self, d: ImageDraw.ImageDraw, pipe, now: float) -> None:
        spoken = pipe.voice.spoken[-1] if pipe.voice.spoken else None
        if spoken and now - spoken[0] <= CAPTION_TTL_S:
            t, text, prio = spoken
            d.rectangle((0, 0, WIN_W, BANNER_H), fill=CAPTION_COLORS.get(prio, CAPTION_COLORS[INFO]))
            d.text((24, 12), "음성 안내  " + time.strftime("%H:%M:%S", time.localtime(t)), font=self.f_small, fill=TEXT)
            d.text((24, 36), text, font=self.f_caption, fill=(255, 255, 255))
        else:
            d.rectangle((0, 0, WIN_W, BANNER_H), fill=(28, 31, 36))
            d.text((24, 12), "음성 안내", font=self.f_small, fill=DIM)
            last = f"마지막 안내: {spoken[1]}" if spoken else "안내 대기 중"
            d.text((24, 42), last, font=self.f_big, fill=DIM)

    def _card(self, d, y: int, h: int, title: str, accent=None) -> int:
        x0, x1 = VIEW_W + 10, WIN_W - 10
        d.rounded_rectangle((x0, y, x1, y + h), 10, fill=CARD)
        if accent:
            d.rounded_rectangle((x0, y, x0 + 6, y + h), 3, fill=accent)
        d.text((x0 + 16, y + 6), title, font=self.f_small, fill=DIM)
        return y + 28

    def _panel(self, d, pipe, now: float) -> None:
        x = VIEW_W + 26
        y = BANNER_H + 8

        # 1. Traffic light
        tl = pipe.tracks.get(pipe.relevant_light) if pipe.relevant_light is not None else None
        state = tl.light.confirmed if tl is not None else None
        color = LIGHT_COLOR.get(state or "", TEXT)
        y0 = self._card(d, y, 92, "신호등", accent=color if state else None)
        lit = LAMPS.get(state or "", (0, 0, 0, 0))
        blink_off = (state or "").startswith("flashing") and int(now * 2) % 2
        for i, (on, c) in enumerate(zip(lit, (RED, YELLOW, GREEN, GREEN))):
            cx = x + 22 + i * 50
            d.ellipse((cx - 20, y0, cx + 20, y0 + 40), fill=c if on and not blink_off else OFF)
            if i == 2:
                d.text((cx - 10, y0 + 6), "←", font=self.f_mid, fill=BG if on else DIM)
        d.text((x + 230, y0 + 2), LIGHT_KO.get(state, "감지 없음") if state else "감지 없음", font=self.f_huge,
               fill=color if state else DIM)
        y += 100

        # 2. Risk
        lead = pipe.tracks.get(pipe.lead_id) if pipe.lead_id is not None else None
        level = lead.collision if lead is not None else "none"
        recent = pipe.last_risk if pipe.last_risk and now - pipe.last_risk[0] < 6.0 else None
        if level == "none" and recent and recent[1] in ("cut_in", "lane"):
            level = "warning"
        lcolor = {"danger": RED, "warning": ORANGE, "none": GREEN}[level]
        y0 = self._card(d, y, 118, "위험 감지", accent=lcolor)
        d.text((x, y0), {"danger": "위험", "warning": "주의", "none": "안전"}[level], font=self.f_huge, fill=lcolor)
        ttc = lead.ttc if lead is not None else None
        d.text((x + 110, y0 + 8), f"충돌까지 {ttc:.1f}초" if ttc else "충돌 위험 없음", font=self.f_mid, fill=TEXT)
        frac = 1.0 - min(ttc, 5.0) / 5.0 if ttc else 0.0
        bx0, bx1 = x + 290, WIN_W - 30
        d.rounded_rectangle((bx0, y0 + 12, bx1, y0 + 28), 6, fill=OFF)
        if frac > 0:
            d.rounded_rectangle((bx0, y0 + 12, bx0 + int((bx1 - bx0) * frac), y0 + 28), 6, fill=lcolor)
        risk_text = f"{recent[2]}  ({now - recent[0]:.0f}초 전)" if recent else "최근 위험 이벤트 없음"
        d.text((x, y0 + 50), risk_text, font=self.f, fill=ORANGE if recent else DIM)
        y += 126

        # 3. Lead vehicle
        y0 = self._card(d, y, 148, "앞차", accent=BLUE if lead is not None else None)
        if lead is None:
            d.text((x, y0 + 10), "앞차 없음", font=self.f_huge, fill=DIM)
        else:
            motion = MOTION_KO.get(lead.motion_smoother.confirmed or "", "판단 중")
            d.text((x, y0), f"ID {pipe.lead_id}", font=self.f_big, fill=TEXT)
            d.text((x + 110, y0), motion, font=self.f_big, fill=BLUE)
            plate = lead.plate
            d.rounded_rectangle((x + 250, y0 - 2, WIN_W - 30, y0 + 40), 6,
                                fill=(245, 245, 240) if plate else OFF)
            d.text((x + 262, y0 + 2), plate or "번호 인식 중", font=self.f_big if plate else self.f_mid,
                   fill=(20, 20, 20) if plate else DIM)
            lamps = lead.lamp_smoother.confirmed or ""
            chips = [("브레이크", "brake_on" in lamps, RED), ("◀ 좌측", "left_turn" in lamps, ORANGE),
                     ("비상등", "hazard" in lamps, ORANGE), ("우측 ▶", "right_turn" in lamps, ORANGE)]
            for j, (label, on, c) in enumerate(chips):
                blink = on and (c is RED or int(now * 3) % 2 == 0)
                cx = x + j * 128
                d.rounded_rectangle((cx, y0 + 54, cx + 118, y0 + 88), 8, fill=c if blink else OFF)
                d.text((cx + 18, y0 + 58), label, font=self.f_mid, fill=TEXT)
        y += 156

        # 4. Lane + ego
        lane_state = pipe.lane_smoother.confirmed
        lane_color = ORANGE if lane_state and "departure" in lane_state else GREEN if lane_state else DIM
        y0 = self._card(d, y, 96, "현재 차선 · 자차", accent=lane_color)
        self._lane_diagram(d, pipe, x, y0 + 2)
        d.text((x + 150, y0), LANE_KO.get(lane_state, "차선 미검출") if lane_state else "차선 미검출",
               font=self.f_big, fill=lane_color)
        d.text((x + 150, y0 + 36), f"자차 {EGO_KO.get(pipe.ego_state, pipe.ego_state)}", font=self.f_mid, fill=TEXT)
        y += 104

        # 5. Analysis table
        c = pipe._scene_counts or {}
        title = f"분석 결과   차량 {c.get('vehicles', 0)} · 보행자 {c.get('person', 0)} · 이륜차 {c.get('two_wheeler', 0)}"
        y0 = self._card(d, y, BANNER_H + VIEW_H - y - 6, title)
        cols = (x, x + 60, x + 160, x + 400)
        for cx, h in zip(cols, ("ID", "객체", "상태", "번호판")):
            d.text((cx, y0), h, font=self.f_small, fill=DIM)
        ry = y0 + 22
        rows = sorted((det for det in pipe.last_dets if det.tid >= 0),
                      key=lambda det: (det.tid != pipe.lead_id, det.name != "traffic_light", det.tid))
        for det in rows:
            if ry > BANNER_H + VIEW_H - 30:
                break
            ts = pipe.tracks.get(det.tid)
            state, plate = self._describe(det, ts)
            fill = BLUE if det.tid == pipe.lead_id else TEXT
            d.text((cols[0], ry), str(det.tid), font=self.f, fill=fill)
            d.text((cols[1], ry), NAME_KO.get(det.name, det.name), font=self.f, fill=fill)
            max_w = cols[3] - cols[2] - 12
            while state and d.textlength(state, font=self.f) > max_w:
                state = state[:-2] + "…"
            d.text((cols[2], ry), state, font=self.f, fill=fill)
            d.text((cols[3], ry), plate, font=self.f, fill=fill)
            ry += 24

    def _describe(self, det, ts) -> tuple[str, str]:
        if ts is None:
            return "-", ""
        if det.name == "traffic_light":
            return LIGHT_KO.get(ts.light.confirmed, "판단 중") if ts.light.confirmed else "판단 중", ""
        if det.name == "person":
            return "보행자 감지", ""
        parts = []
        if ts.motion_smoother.confirmed:
            parts.append(MOTION_KO.get(ts.motion_smoother.confirmed, ts.motion_smoother.confirmed))
        lamps = lamps_ko(ts.lamp_smoother.confirmed)
        if lamps:
            parts.append(lamps)
        if ts.collision != "none":
            parts.append("충돌 " + {"danger": "위험", "warning": "주의"}[ts.collision])
        return (" · ".join(parts) or "추적 중"), (ts.plate or "")

    def _lane_diagram(self, d, pipe, x: int, y: int) -> None:
        """Top-down sketch: our lane's two lines and where our car sits between them."""
        w, h = 120, 60
        d.rectangle((x, y, x + w, y + h), fill=(48, 52, 58))
        lane = pipe.last_lane
        detected = lane is not None and lane.detected
        line_c = (240, 240, 240) if detected else (110, 110, 110)
        for lx in (x + 20, x + w - 20):
            for yy in range(y + 2, y + h, 14):
                d.line((lx, yy, lx, yy + 8), fill=line_c, width=3)
        frac = 0.5
        if lane is not None:
            (lbx, _), _, _, (rbx, _) = lane.polygon
            frame_w = pipe.last_frame_w or (lbx + rbx)
            if rbx > lbx:
                frac = min(max((frame_w / 2 - lbx) / (rbx - lbx), 0.0), 1.0)
        cx = x + 20 + int((w - 40) * frac)
        state = pipe.lane_smoother.confirmed or ""
        d.rounded_rectangle((cx - 9, y + 18, cx + 9, y + 48), 4, fill=ORANGE if "departure" in state else BLUE)

    def _log(self, d, pipe) -> None:
        y = BANNER_H + VIEW_H
        d.rectangle((0, y, WIN_W, WIN_H), fill=(14, 15, 18))
        d.text((16, y + 6), '이벤트 로그   id : [이름] : "상태"', font=self.f_small, fill=DIM)
        link = getattr(pipe, "camera_link", "")
        status = (f"카메라 {link}   " if link else "") + \
            f"{time.strftime('%H:%M:%S')}   {pipe.fps:4.1f} fps   자차 {EGO_KO.get(pipe.ego_state, pipe.ego_state)}"
        d.text((WIN_W - 16 - d.textlength(status, font=self.f_small), y + 6), status, font=self.f_small, fill=DIM)
        labeler = getattr(pipe, "labeler", None)
        session = getattr(pipe, "label_session", None)
        if session is not None:
            if labeler is not None and labeler.error:
                status_txt, color = f"음성 라벨 오류: {labeler.error[:40]}", RED
            elif labeler is not None and labeler.listening:
                status_txt, color = "음성 라벨 듣는 중", GREEN
            else:
                status_txt, color = "음성 라벨 준비 중", DIM
            counts = " ".join(f"{k} {v}" for k, v in sorted(session.counts.items()) if v > 0) or "저장 없음"
            heard = labeler.heard_log[-1][1] if labeler is not None and labeler.heard_log else "-"
            d.text((WIN_W - 560, y + 30), f"{status_txt}   들은 말: {heard}", font=self.f_log, fill=color)
            d.text((WIN_W - 560, y + 52), f"저장된 크롭: {counts}", font=self.f_log, fill=TEXT)
        ly = y + 28
        for ev in reversed(pipe.events.history[-5:]):
            ts = time.strftime("%H:%M:%S", time.localtime(ev.t))
            d.text((16, ly), f"{ts}   {ev.line()}", font=self.f_log, fill=TEXT)
            ly += 21
