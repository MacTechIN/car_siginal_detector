import json
import urllib.request

import numpy as np

from csd.server import EngineServer, build_state
from test_dashboard import fake_pipe


def test_state_has_everything_the_app_shows():
    p = fake_pipe()
    s = build_state(p)
    json.dumps(s, ensure_ascii=False)  # plain JSON types only
    assert s["light"] == {"id": 3, "state": "red_left", "text": "좌회전 (직진 정지)"}
    lead = s["lead"]
    assert lead["id"] == 7 and lead["plate"] == "12가3456" and lead["brake"] and lead["left"]
    assert lead["collision"] == "danger" and lead["ttc"] == 1.6 and lead["motion_text"] == "감속"
    assert s["risk"]["level"] == "danger"
    assert s["caption"]["priority"] == "danger" and "브레이크" in s["caption"]["text"]
    assert s["objects"][0]["id"] == 7 and s["objects"][0]["lead"]
    assert s["lane_text"] == "차선 유지 중" and 0 <= s["lane_pos"] <= 1
    assert s["events"][0]["line"].startswith("7 : [car]")


def test_http_endpoints():
    p = fake_pipe()
    srv = EngineServer(port=0)  # any free port
    srv.start()
    port = srv._httpd.server_address[1]
    try:
        base = f"http://127.0.0.1:{port}"
        assert json.load(urllib.request.urlopen(base + "/health"))["ok"]
        assert urllib.request.urlopen(base + "/frame.jpg").status == 204  # nothing yet
        srv.publish(np.full((120, 160, 3), 80, np.uint8), p.last_dets, p)
        r = urllib.request.urlopen(base + "/frame.jpg")
        assert r.status == 200 and r.read(2) == b"\xff\xd8" and r.headers["X-Frame-Seq"] == "1"
        state = json.loads(urllib.request.urlopen(base + "/state").read().decode("utf-8"))
        assert state["frame_seq"] == 1 and state["lead"]["plate"] == "12가3456"
        req = urllib.request.Request(base + "/shutdown", method="POST")
        urllib.request.urlopen(req)
        assert srv.shutdown_requested.is_set()
    finally:
        srv.stop()
