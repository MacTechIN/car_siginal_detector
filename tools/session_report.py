"""Daily data-collection report: what was recorded, labelled and detected on a given day.

  python tools/session_report.py                 # today
  python tools/session_report.py 2026-10-08

For every recordings/<YYYYmmdd_*> session it reads meta.json, timestamps.csv (frame gaps =
camera/stream outages), labels.csv and crops/, copies the matching logs/events_*.log into the
session folder (so a session folder is self-contained), and writes data_log/<date>.md.
Licence plates are masked in the report (it is committed to git); the raw logs keep them.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAP_S = 5.0  # a pause between frames longer than this is reported as an outage
PLATE_RE = re.compile(r"(\d{2,3}[가-힣])(\d{4})")
EVENT_RE = re.compile(r'^(\d\d:\d\d:\d\d)\.\d+ (\S+) : \[(\w+)\] : "(.*)"$')


def mask_plate(text: str) -> str:
    return PLATE_RE.sub(lambda m: m.group(1) + "****", text)


def hms(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


def read_frames(session: Path) -> tuple[list[float], int]:
    times, nbytes = [], 0
    path = session / "timestamps.csv"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    times.append(float(row["t_unix"]))
                    nbytes += int(row["bytes"])
                except (KeyError, ValueError):
                    pass
    return times, nbytes


def gaps(times: list[float]) -> list[tuple[float, float]]:
    return [(a, b) for a, b in zip(times, times[1:]) if b - a > GAP_S]


def read_labels(session: Path) -> list[dict]:
    path = session / "labels.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def logs_for(times: list[float], log_dir: Path, day: str) -> list[Path]:
    """Event logs whose start time falls inside (or just before) the session."""
    if not times:
        return []
    out = []
    for log in sorted(log_dir.glob(f"events_{day}_*.log")):
        try:
            start = datetime.strptime(log.stem[7:], "%Y%m%d_%H%M%S").timestamp()
        except ValueError:
            continue
        if times[0] - 120 <= start <= times[-1]:
            out.append(log)
    return out


def summarise_events(logs: list[Path]) -> dict:
    by_kind: collections.Counter = collections.Counter()
    states: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    ids: dict[str, set] = collections.defaultdict(set)
    plates: set = set()
    n = 0
    for log in logs:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = EVENT_RE.match(line.strip())
            if not m:
                continue
            n += 1
            _, oid, name, state = m.groups()
            by_kind[name] += 1
            ids[name].add(oid)
            key = state.split(" ttc=")[0]
            if name == "license_plate":
                plates.add(state)
                key = mask_plate(state)
            states[name][key] += 1
    return {"total": n, "by_kind": by_kind, "states": states, "ids": ids, "plates": plates}


def report(day: str) -> Path:
    compact = day.replace("-", "")
    sessions = sorted(p for p in (ROOT / "recordings").glob(f"{compact}_*") if p.is_dir())
    lines = [f"# 수집 자료 기록 — {day}", "",
             f"`python tools/session_report.py {day}`로 {time.strftime('%Y-%m-%d %H:%M')}에 생성했습니다.",
             "원본은 `recordings/`와 `logs/`에 있습니다(git 제외: 번호판·얼굴 포함). 이 보고서의 번호판은 가렸습니다.", ""]
    if not sessions:
        lines.append("이 날짜의 녹화 세션이 없습니다.")
    total_frames = total_bytes = total_dur = 0.0
    summary_rows = []
    details = []
    all_logs: list[Path] = []
    for s in sessions:
        meta = json.loads((s / "meta.json").read_text(encoding="utf-8")) if (s / "meta.json").exists() else {}
        times, nbytes = read_frames(s)
        dur = times[-1] - times[0] if len(times) > 1 else 0.0
        fps = (len(times) - 1) / dur if dur > 0 else 0.0
        g = gaps(times)
        labels = read_labels(s)
        crops = collections.Counter(p.parent.name for p in (s / "crops").rglob("*.jpg")) if (s / "crops").exists() else {}
        logs = logs_for(times, ROOT / "logs", compact)
        all_logs += logs
        for log in logs:  # make the session folder self-contained
            dst = s / log.name
            if not dst.exists() or dst.stat().st_size != log.stat().st_size:
                shutil.copy2(log, dst)
        ev = summarise_events(logs)
        total_frames += len(times)
        total_bytes += nbytes
        total_dur += dur
        stop = meta.get("stop_reason") or ("정상 종료" if meta else "**meta.json 없음 (비정상 종료 또는 진행 중)**")
        summary_rows.append(
            f"| `{s.name}` | {clock(times[0]) if times else '-'} ~ {clock(times[-1]) if times else '-'} | {hms(dur)} | "
            f"{len(times):,} | {fps:.1f} | {nbytes / 1e6:,.0f} MB | {len(g)} | "
            f"{sum(1 for r in labels if r.get('label') not in ('cancel',))} | {sum(crops.values())} |")

        d = [f"### `{s.name}`", "",
             f"- 입력: `{meta.get('source') or meta.get('url') or '-'}`, 종료: {stop}",
             f"- 녹화: {len(times):,}프레임, {hms(dur)}, 평균 {fps:.1f}fps, {nbytes / 1e6:,.0f} MB"]
        settings = meta.get("camera_settings")
        if settings:
            d.append(f"- 카메라 설정: framesize {settings.get('framesize')}, quality {settings.get('quality')}, "
                     f"ae_level {settings.get('ae_level')}, wb_mode {settings.get('wb_mode')}")
        if g:
            d.append(f"- **영상 공백 {len(g)}회** ({GAP_S:.0f}초 넘게 프레임 없음):")
            for a, b in g[:10]:
                d.append(f"  - {clock(a)} ~ {clock(b)} ({hms(b - a)})")
            if len(g) > 10:
                d.append(f"  - … 외 {len(g) - 10}회")
        end_t = datetime.fromisoformat(meta["ended"]).timestamp() if meta.get("ended") else None
        if times and end_t and end_t - times[-1] > GAP_S:
            d.append(f"- **마지막 프레임({clock(times[-1])}) 뒤 종료({clock(end_t)})까지 {hms(end_t - times[-1])} 동안 영상 없음**"
                     " — 카메라 케이블·전원 확인 필요")
        if labels:
            kinds = collections.Counter(r.get("label") for r in labels)
            d.append(f"- 음성 라벨 {len(labels)}건: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
        else:
            d.append("- 음성 라벨: 없음")
        d.append(f"- 학습용 크롭: {sum(crops.values())}장" + (" (" + ", ".join(f"{k} {v}" for k, v in crops.most_common()) + ")" if crops else ""))
        if logs:
            d.append(f"- 이벤트 로그: {', '.join(f'`{p.name}`' for p in logs)} → 세션 폴더에 복사함, 이벤트 {ev['total']:,}건")
            for name, cnt in ev["by_kind"].most_common():
                top = ", ".join(f"{k} {v}" for k, v in ev["states"][name].most_common(6))
                d.append(f"  - `{name}` {cnt}건 (객체 {len(ev['ids'][name])}개): {top}")
        else:
            d.append("- 이벤트 로그: 없음")
        details += d + [""]

    notes = []
    if sessions:
        ev_all = summarise_events(all_logs)
        kinds = ev_all["by_kind"]
        if total_dur > 600 and not kinds.get("traffic_light"):
            notes.append(f"녹화 {hms(total_dur)} 동안 **신호등 이벤트가 0건**입니다. 카메라 방향(신호등이 화면 위쪽에 들어오는지), "
                         "해상도, 검출 크기(`detector.imgsz`)를 확인하세요.")
        if total_dur > 600 and not kinds.get("license_plate"):
            notes.append("번호판 인식이 0건입니다. 앞차가 화면 폭의 18% 이상으로 보여야 시도합니다(`plate.min_box_w_ratio`).")
        dep = ev_all["states"].get("lane", collections.Counter())
        dep_n = sum(v for k, v in dep.items() if k.startswith("departure"))
        if dep_n and dep_n >= dep.get("in_lane", 0):
            notes.append(f"차선 이탈이 {dep_n}회로 차선 유지보다 많거나 같습니다. 카메라가 차량 중앙·정면을 향하는지, "
                         "`lanes.horizon_ratio`가 맞는지 확인하세요.")
        no_crop = [s.name for s in sessions for r in read_labels(s) if r.get("crops") == "0" and r.get("label") != "cancel"]
        if no_crop:
            notes.append(f"신호등 없이 기록된 음성 라벨 {len(no_crop)}건(크롭 0장) — 신호등이 화면에 검출될 때 말해야 학습 데이터가 됩니다.")
    if notes:
        lines += ["## 확인할 점", "", *[f"- {n}" for n in notes], ""]
    if sessions:
        lines += ["## 요약", "",
                  f"- 세션 {len(sessions)}개, 녹화 {hms(total_dur)}, {int(total_frames):,}프레임, {total_bytes / 1e6:,.0f} MB", "",
                  "| 세션 | 시간 | 길이 | 프레임 | fps | 크기 | 영상 공백 | 음성 라벨 | 크롭 |",
                  "|---|---|---|---|---|---|---|---|---|", *summary_rows, "", "## 세션별 상세", "", *details]
        unmatched = sorted(set((ROOT / "logs").glob(f"events_{compact}_*.log")) - set(all_logs))
        if unmatched:
            lines += ["## 세션과 짝이 없는 이벤트 로그", "",
                      *[f"- `{p.name}` ({p.stat().st_size:,} bytes)" for p in unmatched], ""]
    out = ROOT / "data_log" / f"{day}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily data-collection report")
    ap.add_argument("date", nargs="?", default=time.strftime("%Y-%m-%d"), help="YYYY-MM-DD (default: today)")
    a = ap.parse_args()
    out = report(a.date)
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
