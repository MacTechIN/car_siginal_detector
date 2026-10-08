import json
import math
import socket
import threading
import time

import pytest

from csd.cameras import Camera, CameraIndex, load_cameras
from csd.gps import GpsSource, bearing_deg, checksum_ok, haversine_m, parse_rmc
from csd.guidance import Guidance
from csd.tts import SIGNAL, WARNING

RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"  # textbook sentence


def nmea(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def rmc(lat: float, lon: float, kmh: float, course: float | None) -> str:
    def dm(v, deg_w):
        d = int(abs(v))
        return f"{d:0{deg_w}d}{(abs(v) - d) * 60:07.4f}"
    c = "" if course is None else f"{course:.1f}"
    return nmea(f"GNRMC,120000.00,A,{dm(lat, 2)},N,{dm(lon, 3)},E,{kmh / 1.852:.2f},{c},081026,,,A")


def test_parse_textbook_rmc():
    assert checksum_ok(RMC)
    f = parse_rmc(RMC, 1.0)
    assert f.lat == pytest.approx(48.1173, abs=1e-4) and f.lon == pytest.approx(11.5167, abs=1e-4)
    assert f.speed_kmh == pytest.approx(22.4 * 1.852, abs=0.01) and f.course == pytest.approx(84.4)


def test_void_or_corrupt_sentences_ignored():
    assert parse_rmc(RMC.replace(",A,", ",V,"), 0) is None          # no fix (bad checksum too)
    assert parse_rmc(RMC[:-1] + "B", 0) is None                       # checksum mismatch
    assert parse_rmc(nmea("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,,,,"), 0) is None


def test_course_from_positions_when_missing():
    g = GpsSource("tcp://unused:1")
    g.feed_line(rmc(37.5000, 127.0, 50, None), t=1.0)
    f = g.feed_line(rmc(37.5003, 127.0, 50, None), t=2.0)  # ~33 m north
    assert f.course == pytest.approx(0, abs=1)
    assert g.latest(now=2.5) is f and g.latest(now=10.0) is None  # stale after 3 s


def test_tcp_nmea_source():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def phone():
        conn, _ = srv.accept()
        with conn:
            for _ in range(20):
                try:
                    conn.sendall((rmc(37.55, 126.97, 42.0, 90.0) + "\r\n").encode())
                except OSError:  # the reader has stopped
                    return
                time.sleep(0.05)

    threading.Thread(target=phone, daemon=True).start()
    g = GpsSource(f"tcp://127.0.0.1:{port}").start()
    try:
        deadline = time.time() + 3
        while g.latest() is None and time.time() < deadline:
            time.sleep(0.02)
        f = g.latest()
        assert f is not None and f.speed_kmh == pytest.approx(42.0, abs=0.1) and f.course == 90.0
    finally:
        g.stop()
        srv.close()


def test_geometry():
    assert haversine_m(37.5, 127.0, 37.509, 127.0) == pytest.approx(1000.8, rel=0.01)
    assert bearing_deg(37.5, 127.0, 37.6, 127.0) == pytest.approx(0, abs=0.1)
    assert bearing_deg(37.5, 127.0, 37.5, 127.1) == pytest.approx(90, abs=0.1)


def test_load_portal_csv_cp949_and_api_json(tmp_path):
    csv_path = tmp_path / "cams.csv"
    csv_path.write_bytes(("무인교통단속카메라관리번호,위도,경도,제한속도,단속구분,보호구역구분,설치장소\n"
                          "A1,37.5054,127.0000,60,1,,교차로 앞\n"
                          "A2,37.5,127.01,0,4,,주정차\n"
                          "BAD,0,0,60,1,,좌표 없음\n").encode("cp949"))
    cams = load_cameras(csv_path)
    assert [c.id for c in cams] == ["A1", "A2"] and cams[0].limit == 60 and cams[0].place == "교차로 앞"
    assert cams[0].is_speed and not cams[1].is_speed

    api = {"response": {"header": {"resultCode": "00"}, "body": {"items": [
        {"mnlssRegltCameraManageNo": "B1", "latitude": "36.81", "longitude": "127.79", "lmttVe": "30",
         "prtcareaType": "1"}]}}}
    (tmp_path / "cams.json").write_text(json.dumps(api), encoding="utf-8")
    b = load_cameras(tmp_path / "cams.json")[0]
    assert b.id == "B1" and b.limit == 30 and b.in_protected_zone


def test_index_ahead_only_in_driving_direction():
    idx = CameraIndex([Camera("N", 37.5054, 127.0, 60), Camera("S", 37.4946, 127.0, 60),
                       Camera("FAR", 37.6, 127.0, 60)])
    assert [c.id for _, c in idx.near(37.5, 127.0, 700)] in (["N", "S"], ["S", "N"])
    assert [c.id for _, c in idx.ahead(37.5, 127.0, 0.0, 700)] == ["N"]     # heading north
    assert [c.id for _, c in idx.ahead(37.5, 127.0, 180.0, 700)] == ["S"]   # heading south


def drive(guidance, kmh, start_lat=37.5000, seconds=40, now0=1000.0):
    """Drive north at constant speed; returns [(t, text, priority, key)] spoken."""
    from csd.gps import Fix
    lat = start_lat
    for i in range(seconds):
        guidance.update(Fix(now0 + i, lat, 127.0, kmh, 0.0), now0 + i)
        lat += kmh / 3.6 / 111_195  # metres per second -> degrees of latitude


def make(cams):
    said, events = [], []
    g = Guidance(CameraIndex(cams), say=lambda item, key, cd: said.append((item[0], item[1], key)),
                 emit=lambda oid, name, state: events.append((oid, name, state)))
    return g, said, events


def test_camera_announced_once_and_no_speeding_at_legal_speed():
    g, said, events = make([Camera("A1", 37.5054, 127.0, 60)])  # ~600 m north
    drive(g, 55)
    texts = [t for t, _, _ in said]
    assert texts[0] == "GPS가 연결되었습니다"
    assert texts[1] == "반경 2킬로미터 안에 과속 단속 카메라 1대가 있습니다"
    ahead = [t for t in texts if "앞 과속 단속 카메라" in t]
    assert len(ahead) == 1 and ahead[0].endswith("제한속도 60킬로미터")
    assert int(ahead[0].split("미터")[0]) in (400, 500)
    assert not any("과속입니다" in t or "줄이세요" in t for t in texts)
    assert ("A1", "speed_camera") == events[1][:2]


def test_speeding_warned_and_slow_down_near_camera():
    g, said, events = make([Camera("A1", 37.5054, 127.0, 60, zone="1")])
    drive(g, 80)
    texts = [(t, p) for t, p, _ in said]
    first = next((t, p) for t, p in texts if "앞 과속 단속 카메라" in t)
    assert first[0].startswith("보호구역, ") and first[1] == WARNING  # already too fast
    assert ("속도를 줄이세요. 제한속도 60킬로미터", WARNING) in texts
    assert any(t == "과속입니다. 제한속도 60킬로미터" for t, _ in texts)
    assert any(name == "speeding" and state.startswith("over") for _, name, state in events)
    assert events[-1][1:] == ("speeding", "within_limit")  # zone left after the camera


def test_no_announcement_for_non_speed_camera_or_without_course():
    g, said, _ = make([Camera("P", 37.5054, 127.0, 0)])  # parking camera
    drive(g, 80)
    assert not any("카메라" in t and "앞" in t for t, _, _ in said)
    from csd.gps import Fix
    g2, said2, _ = make([Camera("A1", 37.5010, 127.0, 60)])
    g2.update(Fix(1.0, 37.5, 127.0, 0.0, None), 1.0)  # standing still: no course
    assert not any("앞 과속" in t for t, _, _ in said2)


def test_gps_lost_announced_after_timeout():
    from csd.gps import Fix
    g, said, events = make([])
    g.update(Fix(1.0, 37.5, 127.0, 30.0, 0.0), 1.0)
    g.update(None, 5.0)
    assert g.state.gps_ok
    g.update(None, 12.0)
    assert not g.state.gps_ok and said[-1][0] == "GPS 신호가 끊겼습니다" and events[-1] == (0, "gps", "lost")


def test_fetch_cameras_from_file(tmp_path):
    import tools_path  # noqa: F401
    import fetch_cameras
    src = tmp_path / "dl.csv"
    src.write_text("위도,경도,제한속도,설치장소\n37.5,127.0,50,A\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    fetch_cameras.write(load_cameras(src), out)
    back = load_cameras(out)
    assert len(back) == 1 and back[0].limit == 50 and back[0].place == "A"
    assert math.isclose(back[0].lat, 37.5)
