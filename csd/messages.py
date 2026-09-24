"""Korean voice phrases and priorities for each (object, state)."""

from __future__ import annotations

from .tts import DANGER, INFO, SIGNAL, WARNING

TRAFFIC_LIGHT = {
    "red": "빨간불입니다",
    "yellow": "노란불입니다",
    "green": "초록불입니다",
    "left": "좌회전 신호입니다",
    "green_left": "직진 좌회전 동시 신호입니다",
    "red_left": "좌회전 신호입니다. 직진은 정지하세요",
    "red_yellow": "곧 신호가 바뀝니다",
    "flashing_yellow": "황색 점멸 신호입니다. 주의해서 진행하세요",
    "flashing_red": "적색 점멸 신호입니다. 일시 정지 후 진행하세요",
    "off": "신호등이 꺼져 있습니다",
}

LEAD_MOTION = {
    "stopped": "앞차가 정지해 있습니다",
    "starting": "앞차가 출발했습니다",
    "moving": "앞차 주행 중입니다",
    "slowing": "앞차가 감속하고 있습니다",
}

TURN = {
    "left_turn": "앞차 왼쪽 방향지시등",
    "right_turn": "앞차 오른쪽 방향지시등",
    "hazard": "앞차 비상등이 켜졌습니다",
}

_DIGITS = "영일이삼사오육칠팔구"


def spell_plate(plate: str) -> str:
    """'12가3456' -> '일 이 가, 삼 사 오 육' so the voice reads digit by digit."""
    out = []
    for ch in plate:
        out.append(_DIGITS[int(ch)] if ch.isdigit() else ch + ",")
    return " ".join(out)


def traffic_light(state: str) -> tuple[str, int] | None:
    text = TRAFFIC_LIGHT.get(state)
    return (text, SIGNAL) if text else None


def lead_motion(state: str) -> tuple[str, int] | None:
    text = LEAD_MOTION.get(state)
    return (text, INFO if state in ("moving",) else SIGNAL) if text else None


def brake(on: bool) -> tuple[str, int] | None:
    return ("앞차 브레이크", WARNING) if on else None


def turn(state: str) -> tuple[str, int] | None:
    text = TURN.get(state)
    return (text, SIGNAL) if text else None


def plate(text: str) -> tuple[str, int]:
    return f"앞차 번호 {spell_plate(text)}", INFO


def collision(level: str) -> tuple[str, int] | None:
    if level == "danger":
        return "전방 충돌 위험! 브레이크!", DANGER
    if level == "warning":
        return "전방 충돌 주의", WARNING
    return None


def cut_in(side: str) -> tuple[str, int]:
    return ("왼쪽 차량 끼어들기 주의" if side == "cut_in_left" else "오른쪽 차량 끼어들기 주의"), WARNING


def lane_departure(side: str) -> tuple[str, int] | None:
    if side == "left":
        return "왼쪽 차선을 벗어나고 있습니다", WARNING
    if side == "right":
        return "오른쪽 차선을 벗어나고 있습니다", WARNING
    return None


def scene(counts: dict[str, int]) -> tuple[str, int] | None:
    vehicles = counts.get("vehicles", 0)
    parts = []
    if vehicles:
        parts.append(f"주변 차량 {vehicles}대")
    if counts.get("person"):
        parts.append(f"보행자 {counts['person']}명")
    if counts.get("two_wheeler"):
        parts.append(f"이륜차 {counts['two_wheeler']}대")
    return (", ".join(parts), INFO) if parts else None
