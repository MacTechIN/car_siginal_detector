"""Fixed traffic-enforcement cameras from Korea's public standard data.

Source: 공공데이터포털 "전국무인교통단속카메라표준데이터" (National Police Agency; fixed cameras
only, updated twice a year). Download the CSV (or JSON) from data.go.kr, or fetch it with
tools/fetch_cameras.py, into data/speed_cameras.csv (git-ignored).

The file comes with Korean headers (CSV download) or English keys (open API, and the
older per-city APIs), so columns are matched by a list of names. The meaning of the
enforcement-type code (단속구분) is not documented on the portal, so it is kept but not
interpreted: a camera with a speed limit above 0 is treated as a speed camera.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .gps import bearing_deg, haversine_m

FIELDS = {
    "id": ["무인교통단속카메라관리번호", "무인교통단속카메라 관리번호", "mnlssRegltCameraManageNo", "unatEnfoNum"],
    "lat": ["위도", "latitude", "lat"],
    "lon": ["경도", "longitude", "lot", "lon"],
    "limit": ["제한속도", "lmttVe", "limitSpeed"],
    "kind": ["단속구분", "regltSe", "crcdClas"],
    "zone": ["보호구역구분", "prtcareaType", "proAreaDiv"],
    "road": ["도로노선명", "roadRouteNm", "roadNm"],
    "place": ["설치장소", "itlpc", "installPlace"],
    "section": ["단속구간위치구분", "regltSctnLcSe", "inteLocDiv"],
}


@dataclass
class Camera:
    id: str
    lat: float
    lon: float
    limit: int           # km/h, 0 = not a speed camera (signal, parking, ...)
    kind: str = ""       # 단속구분 code as given
    zone: str = ""       # 보호구역구분 code as given (blank = none)
    road: str = ""
    place: str = ""
    section: str = ""

    @property
    def is_speed(self) -> bool:
        return self.limit > 0

    @property
    def in_protected_zone(self) -> bool:
        return self.zone.strip() not in ("", "0")


def _pick(row: dict, key: str) -> str:
    for name in FIELDS[key]:
        v = row.get(name)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _to_camera(row: dict) -> Camera | None:
    try:
        lat, lon = float(_pick(row, "lat")), float(_pick(row, "lon"))
    except ValueError:
        return None
    if not (33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.0):  # outside South Korea / swapped
        return None
    try:
        limit = int(float(_pick(row, "limit") or 0))
    except ValueError:
        limit = 0
    return Camera(_pick(row, "id"), lat, lon, limit, _pick(row, "kind"), _pick(row, "zone"),
                  _pick(row, "road"), _pick(row, "place"), _pick(row, "section"))


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):  # portal CSVs are often CP949
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _json_rows(data) -> list[dict]:
    """Rows from a portal JSON file or an API response ({"response": {"body": {"items": ...}}})."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("records", "items", "item", "data", "body", "response"):
            if key in data:
                rows = _json_rows(data[key])
                if rows:
                    return rows
    return []


def load_cameras(path: str | Path) -> list[Camera]:
    path = Path(path)
    text = _read_text(path)
    if path.suffix.lower() == ".json":
        rows = _json_rows(json.loads(text))
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    cams = [c for c in (_to_camera(r) for r in rows) if c is not None]
    return cams


class CameraIndex:
    """Grid lookup of cameras near a position (cells of 0.01 degree, about 1 km)."""

    CELL = 0.01

    def __init__(self, cameras: list[Camera]):
        self.cameras = cameras
        self._grid: dict[tuple[int, int], list[Camera]] = {}
        for c in cameras:
            self._grid.setdefault(self._cell(c.lat, c.lon), []).append(c)

    def __len__(self) -> int:
        return len(self.cameras)

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return int(math.floor(lat / self.CELL)), int(math.floor(lon / self.CELL))

    def near(self, lat: float, lon: float, radius_m: float) -> list[tuple[float, Camera]]:
        """(distance m, camera) within `radius_m`, nearest first."""
        n = int(math.ceil(radius_m / 800.0))  # a cell is >= ~880 m wide at Korean latitudes
        ci, cj = self._cell(lat, lon)
        out = []
        for i in range(ci - n, ci + n + 1):
            for j in range(cj - n, cj + n + 1):
                for c in self._grid.get((i, j), ()):
                    d = haversine_m(lat, lon, c.lat, c.lon)
                    if d <= radius_m:
                        out.append((d, c))
        out.sort(key=lambda dc: dc[0])
        return out

    def ahead(self, lat: float, lon: float, course: float, radius_m: float,
              cone_deg: float = 30.0) -> list[tuple[float, Camera]]:
        """Cameras within `radius_m` whose bearing is within `cone_deg` of the course.

        The data has no reliable facing direction, so a camera for the opposite carriageway
        of the same road also counts as ahead.
        """
        out = []
        for d, c in self.near(lat, lon, radius_m):
            diff = abs((bearing_deg(lat, lon, c.lat, c.lon) - course + 180) % 360 - 180)
            if diff <= cone_deg or d < 30:
                out.append((d, c))
        return out
