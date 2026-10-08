"""Driver guidance from GPS: enforcement cameras ahead, the speed limit there, speeding.

Voice only (the dashboard is left as it is). Speed limits come from the camera data, so a
limit is known around speed cameras (approach zone and just after the camera), not on
every road. Announcements per camera:
  ~500 m   "500미터 앞 과속 단속 카메라, 제한속도 60킬로미터"  (once)
  <=200 m  "속도를 줄이세요. 제한속도 60킬로미터"  (only when over the limit)
Over the limit anywhere in a camera zone: "과속입니다. 제한속도 60킬로미터" (repeats).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cameras import Camera, CameraIndex
from .gps import Fix
from .tts import INFO, SIGNAL, WARNING


@dataclass
class GuidanceConfig:
    announce_m: float = 500.0      # first announcement of a camera ahead
    near_m: float = 200.0          # "slow down" if over the limit this close
    zone_after_m: float = 100.0    # the limit still applies this far past the camera
    margin_kmh: float = 5.0        # tolerated speed over the limit before warning
    cone_deg: float = 30.0         # camera counts as ahead within this angle of the course
    summary_radius_m: float = 2000.0
    lost_after_s: float = 10.0     # announce "GPS lost" after this long without a fix


@dataclass
class GuidanceState:
    speed_kmh: float | None = None
    limit_kmh: int | None = None       # current limit (camera zone), None if unknown
    next_camera_m: float | None = None
    next_camera: Camera | None = None
    speeding: bool = False
    gps_ok: bool = False


@dataclass
class Guidance:
    cameras: CameraIndex
    say: callable                     # say((text, priority), key, cooldown_s)
    emit: callable                    # emit(obj_id, name, state)
    cfg: GuidanceConfig = field(default_factory=GuidanceConfig)
    state: GuidanceState = field(default_factory=GuidanceState)

    def __post_init__(self):
        self._stage: dict[str, int] = {}   # camera id -> 1 announced, 2 "slow down" given
        self._had_fix = False
        self._last_fix_t: float | None = None
        self._summary_done = False
        self._was_speeding = False

    @staticmethod
    def _km(limit: int) -> str:
        return f"제한속도 {limit}킬로미터"

    def update(self, fix: Fix | None, now: float) -> GuidanceState:
        st = self.state
        if fix is None:
            if self._had_fix and st.gps_ok and now - (self._last_fix_t or now) >= self.cfg.lost_after_s:
                st.gps_ok = False
                self.emit(0, "gps", "lost")
                self.say(("GPS 신호가 끊겼습니다", INFO), "gps_lost", 60.0)
            if not st.gps_ok:
                st.speed_kmh = st.limit_kmh = st.next_camera_m = None
                st.next_camera = None
                st.speeding = False
            return st

        self._last_fix_t = fix.t
        if not st.gps_ok:
            st.gps_ok = True
            self.emit(0, "gps", "fix")
            if not self._had_fix:
                self.say(("GPS가 연결되었습니다", INFO), "gps_fix", 0.0)
            self._had_fix = True
        st.speed_kmh = fix.speed_kmh

        if not self._summary_done and len(self.cameras):
            self._summary_done = True
            n = sum(1 for _, c in self.cameras.near(fix.lat, fix.lon, self.cfg.summary_radius_m) if c.is_speed)
            km = self.cfg.summary_radius_m / 1000
            self.say((f"반경 {km:g}킬로미터 안에 과속 단속 카메라 {n}대가 있습니다", INFO), "cam_summary", 0.0)

        # Camera zone and the limit that applies here.
        ahead: list[tuple[float, Camera]] = []
        if fix.course is not None:
            ahead = [(d, c) for d, c in self.cameras.ahead(fix.lat, fix.lon, fix.course,
                                                           self.cfg.announce_m + 100, self.cfg.cone_deg)
                     if c.is_speed]
        just_passed = [(d, c) for d, c in self.cameras.near(fix.lat, fix.lon, self.cfg.zone_after_m)
                       if c.is_speed and all(c.id != a.id for _, a in ahead)]
        st.next_camera_m, st.next_camera = (ahead[0] if ahead else (None, None))
        zone = ahead[0][1] if ahead else (just_passed[0][1] if just_passed else None)
        st.limit_kmh = zone.limit if zone else None
        st.speeding = bool(zone and fix.speed_kmh > zone.limit + self.cfg.margin_kmh)

        warned = False
        if ahead:
            d, cam = ahead[0]
            stage = self._stage.get(cam.id, 0)
            prefix = "보호구역, " if cam.in_protected_zone else ""
            if stage < 1 and d <= self.cfg.announce_m:
                dist = max(100, int(d // 100) * 100)
                self._stage[cam.id] = 1
                self.emit(cam.id or "cam", "speed_camera", f"ahead {int(d)}m limit={cam.limit}")
                self.say((f"{prefix}{dist}미터 앞 과속 단속 카메라, {self._km(cam.limit)}",
                          WARNING if st.speeding else SIGNAL), "cam_ahead", 0.0)
            elif stage < 2 and d <= self.cfg.near_m and st.speeding:
                self._stage[cam.id] = 2
                self.say((f"속도를 줄이세요. {self._km(cam.limit)}", WARNING), "cam_slow", 0.0)
                warned = True
        if st.speeding != self._was_speeding:  # log changes only
            self._was_speeding = st.speeding
            self.emit(0, "speeding", f"over speed={fix.speed_kmh:.0f} limit={zone.limit}" if st.speeding
                      else "within_limit")
        if st.speeding and not warned:
            self.say((f"과속입니다. {self._km(zone.limit)}", WARNING), "speeding", 6.0)

        # Forget cameras left behind so they are announced again on the next pass.
        near_ids = {c.id for _, c in self.cameras.near(fix.lat, fix.lon, 1500)}
        for cid in [k for k in self._stage if k not in near_ids]:
            del self._stage[cid]
        return st
