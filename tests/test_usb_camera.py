import json
import struct

import cv2
import numpy as np

from csd.usb_camera import HEADER, PacketParser


def jpeg(v=100) -> bytes:
    return cv2.imencode(".jpg", np.full((16, 16, 3), v, np.uint8))[1].tobytes()


def packet(kind: bytes, payload: bytes, ms: int = 1234) -> bytes:
    return HEADER.pack(kind, len(payload), ms) + payload


def test_frames_and_status_across_tiny_chunks():
    frames = [jpeg(10), jpeg(200)]
    status = json.dumps({"framesize": 12, "streaming": 1}).encode()
    stream = b"boot log noise" + packet(b"CSDF", frames[0]) + packet(b"CSDJ", status) + packet(b"CSDF", frames[1])
    p, out = PacketParser(), []
    for i in range(0, len(stream), 5):
        out += p.feed(stream[i:i + 5])
    assert [k for k, _, _ in out] == [b"CSDF", b"CSDJ", b"CSDF"]
    assert out[0][2] == frames[0] and out[2][2] == frames[1]
    assert json.loads(out[1][2])["framesize"] == 12


def test_resync_after_lost_bytes_and_corrupt_frame():
    good = jpeg(50)
    broken = packet(b"CSDF", good)[:-40]            # truncated: its tail is lost
    fake = packet(b"CSDF", b"not a jpeg at all")    # right length, wrong content
    stream = broken + fake + packet(b"CSDF", good)
    out = PacketParser().feed(stream)
    frames = [pl for k, _, pl in out if k == b"CSDF"]
    assert frames and frames[-1] == good
    assert all(f[:2] == b"\xff\xd8" for f in frames)


def test_rejects_absurd_length():
    stream = b"CSDF" + struct.pack("<II", 50_000_000, 0) + packet(b"CSDF", jpeg(1))
    out = PacketParser().feed(stream)
    assert len(out) == 1 and out[0][2] == jpeg(1)
