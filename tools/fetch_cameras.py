"""Enforcement-camera data for GPS guidance -> data/speed_cameras.csv (git-ignored).

Source: 공공데이터포털 "전국무인교통단속카메라표준데이터" (National Police Agency, fixed
cameras, updated twice a year).

  1) Downloaded file (no API key needed, a data.go.kr login is):
       data.go.kr -> 전국무인교통단속카메라표준데이터 -> CSV download, then
       python tools/fetch_cameras.py --file 다운로드.csv
  2) Open API (apply for the dataset's API on data.go.kr to get a serviceKey):
       python tools/fetch_cameras.py --key <serviceKey>      (or set CSD_DATA_GO_KR_KEY)

Either way the rows are checked (position inside Korea), normalised to the Korean column
names and written to data/speed_cameras.csv.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from csd.cameras import FIELDS, _json_rows, _to_camera, load_cameras  # noqa: E402

OUT = ROOT / "data" / "speed_cameras.csv"
# Standard-data API on data.go.kr (tn_pubr_public_* naming used by the standard datasets).
API_URL = "http://api.data.go.kr/openapi/tn_pubr_public_unmanned_traffic_camera_api"
COLUMNS = ["id", "lat", "lon", "limit", "kind", "zone", "road", "place", "section"]


def write(cams, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([FIELDS[c][0] for c in COLUMNS])  # Korean header names
        for c in cams:
            w.writerow([getattr(c, k) for k in COLUMNS])


def from_api(key: str, rows_per_page: int = 1000) -> list:
    cams, page = [], 1
    while True:
        r = requests.get(API_URL, params={"serviceKey": key, "pageNo": page, "numOfRows": rows_per_page,
                                          "type": "json"}, timeout=30)
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            raise SystemExit(f"API did not return JSON (key not approved yet?):\n{r.text[:300]}")
        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") not in (None, "00"):
            raise SystemExit(f"API error {header.get('resultCode')}: {header.get('resultMsg')}")
        rows = _json_rows(data)
        if not rows:
            break
        cams += [c for c in (_to_camera(x) for x in rows) if c is not None]
        total = int(data.get("response", {}).get("body", {}).get("totalCount", 0) or 0)
        print(f"  page {page}: {len(cams)}/{total or '?'}", flush=True)
        if total and page * rows_per_page >= total:
            break
        page += 1
    return cams


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--file", help="CSV/JSON downloaded from data.go.kr")
    src.add_argument("--key", default=os.environ.get("CSD_DATA_GO_KR_KEY"), help="data.go.kr serviceKey")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.file:
        cams = load_cameras(a.file)
    elif a.key:
        cams = from_api(a.key)
    else:
        ap.error("give --file <download> or --key <serviceKey> (see the help text)")
    if not cams:
        raise SystemExit("no usable rows (check the file's columns: 위도, 경도, 제한속도 ...)")
    write(cams, Path(a.out))
    speed = [c for c in cams if c.is_speed]
    print(f"saved {len(cams)} cameras ({len(speed)} speed cameras, "
          f"{sum(c.in_protected_zone for c in speed)} in protected zones) -> {a.out}")


if __name__ == "__main__":
    main()
