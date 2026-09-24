import cv2
import numpy as np

from csd.stream import FileSource, split_jpegs


def jpeg(value: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((8, 8, 3), value, np.uint8))
    return buf.tobytes()


def part(jpg: bytes) -> bytes:
    return (b"--123456789000000000000987654321\r\nContent-Type: image/jpeg\r\n"
            b"Content-Length: %d\r\n\r\n" % len(jpg)) + jpg + b"\r\n"


def test_split_keeps_every_frame_in_order_across_chunks():
    frames = [jpeg(v) for v in (10, 120, 240)]
    stream = b"".join(part(f) for f in frames)
    buf, out = bytearray(), []
    for i in range(0, len(stream), 7):  # tiny chunks split markers and headers
        buf += stream[i:i + 7]
        out += split_jpegs(buf)
    assert out == frames


def test_split_leaves_incomplete_frame_in_buffer():
    f = jpeg(50)
    buf = bytearray(part(f) + b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n" + f[:30])
    assert split_jpegs(buf) == [f]
    assert buf.startswith(b"\xff\xd8")


def make_recording(folder, times):
    folder.mkdir()
    lines = ["index,file,t_unix,t_rel,bytes"]
    for i, t in enumerate(times):
        data = jpeg((i * 20) % 256)
        (folder / f"{i:06d}.jpg").write_bytes(data)
        lines.append(f"{i},{i:06d}.jpg,{1000 + t:.3f},{t:.3f},{len(data)}")
    (folder / "timestamps.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_replay_uses_recorded_times(tmp_path):
    rec = tmp_path / "녹화_한글경로"  # non-ASCII path must work on Windows
    times = [0.0, 0.1, 0.35, 0.4]
    make_recording(rec, times)
    src = FileSource(str(rec)).start()
    got = []
    seq = -1
    while True:
        seq, t, img = src.read(seq)
        if img is None:
            break
        got.append(t)
    assert len(got) == 4
    rel = [g - got[0] for g in got]
    assert np.allclose(rel, times, atol=1e-6)


def test_realtime_replay_skips_late_frames(tmp_path):
    rec = tmp_path / "rec"
    make_recording(rec, [i * 0.02 for i in range(20)])  # 50 fps recording, 0.4 s
    src = FileSource(str(rec), realtime=True).start()
    seq, n = -1, 0
    import time
    while True:
        seq, t, img = src.read(seq)
        if img is None:
            break
        n += 1
        time.sleep(0.1)  # a slow detector: ~10 fps
    assert n < 12
