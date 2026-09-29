"""
core/flight_state.py

Takes normalized telemetry dicts (as produced by any connector, e.g.
connectors.msfs.MSFSConnector.read()) one tick at a time, and turns them
into flight-level facts: block start/end, distance flown, fuel burned,
landing quality, gear/flap speed checks, and taxi timing.

This module knows nothing about SimConnect, UDP, or any specific sim -
it only understands the normalized dict shape. That's what lets the same
tracker serve MSFS, P3D, and X-Plane connectors later.

Landing rate: a flight's landing_vs is its HARDEST touchdown (most
negative), so a bounce can't hide a hard first landing. Two sources:
  - Sampled (preferred): connectors that watch touchdowns many times a
    second (connectors/msfs.py, 20x) pass the rates caught since the last
    tick in data["touchdowns"]. Only touchdowns after the flight has been
    airborne count.
  - 1 s fallback: when no sampled touchdown was caught (connector without
    a sampler, or the sampler failed), each touchdown seen at the normal
    ~1 s tick is the more negative of the last airborne reading and the
    first on-ground one - the first on-ground reading alone is usually
    taken after the aircraft has settled or is rebounding on its gear.
block_end reports which one was used in "landing_vs_source" ("sampled" or
"1s"), plus every sampled touchdown rate in "sampled_touchdowns".

Touchdown details (for the landing grade and the debrief): block_end's
"landing" is the hardest touchdown as {"vs", "g", "bank", "pitch", "ias",
"gs", "time"} (whatever the connector could give), "touchdowns" all of
them, and "bounces" how many came within BOUNCE_WINDOW_S of the one
before. landing_g is the hardest touchdown's G.

Flight record (for the debrief), also in block_end:
  - "track": a point every TRACK_INTERVAL_S and at each event, as
    [seconds since block start, lat, lon, altitude ft, IAS kt, GS kt]
  - "timeline": block_start, takeoff, touchdown, gear, flaps and
    block_end events, each {"type", "time", ...}

Usage:
    tracker = FlightStateTracker()
    result = tracker.update(data)   # data = connector.read() dict
    result = tracker.update(data, can_start_block=False)   # engine start ignored
    # result["state"]  -> current state string
    # result["events"] -> list of event dicts that just fired this tick
    # result["live"]   -> running stats (distance, fuel burned, elapsed time)
"""

import time
import math

TRACK_INTERVAL_S = 20
BOUNCE_WINDOW_S = 30


def _round(value, digits=0):
    if not isinstance(value, (int, float)):
        return None
    return round(value, digits) if digits else int(round(value))


def _as_touchdown(item):
    """A connector touchdown -> {"vs", "g", "bank", "pitch", "ias", "gs"}
    (a bare number is an older connector's rate only), or None."""
    if isinstance(item, (int, float)):
        return {"vs": item}
    if isinstance(item, dict) and isinstance(item.get("vs"), (int, float)):
        return dict(item)
    return None


def _touchdown_from_tick(vs, data):
    """The 1 s fallback's touchdown details, from the tick it was seen on."""
    return {"vs": vs, "g": data.get("g_force"), "bank": data.get("bank"),
            "pitch": data.get("pitch"), "ias": data.get("airspeed_indicated"),
            "gs": data.get("ground_velocity")}


def _touchdown_vs(airborne_vs, ground_vs):
    """The more negative of the last airborne and first on-ground vertical
    speed readings (fpm), ignoring missing ones. None if both are missing."""
    readings = [v for v in (airborne_vs, ground_vs) if isinstance(v, (int, float))]
    return min(readings) if readings else None


def _haversine_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in nautical miles."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


class FlightStateTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all tracking state - call this after a completed flight if reusing the tracker."""
        self.state = "IDLE"
        self.was_airborne = False

        self.block_start_time = None
        self.block_end_time = None
        self.block_hours = None
        self._prev = {}
        self.g_force_peak = None
        self.landing_g = None
        self._reset_landing()
        self._reset_record()
        self.distance_nm = 0.0
        self.fuel_at_block_start = None
        self.fuel_at_block_end = None
        self.fuel_burned = None
        self.aircraft_title = None
        self.aircraft_atc_type = None
        self.aircraft_atc_model = None
        self.aircraft_atc_id = None
        self.dep_lat = None
        self.dep_lon = None
        self.arr_lat = None
        self.arr_lon = None

    def _reset_landing(self):
        self.landing_vs = None
        self.landing_vs_source = None
        self._sampled_touchdowns = []
        self._fallback_landing_vs = None
        self._sampled_details = []
        self._fallback_details = []
        self.landing = None
        self.bounces = 0

    def _reset_record(self):
        self.track = []
        self.timeline = []
        self._last_track_time = None

    def _update_landing_vs(self):
        """landing_vs = hardest sampled touchdown if any were caught,
        else the hardest 1 s fallback reading. `landing` is that
        touchdown's details and `bounces` the touchdowns that came within
        BOUNCE_WINDOW_S of the one before."""
        if self._sampled_touchdowns:
            self.landing_vs = min(self._sampled_touchdowns)
            self.landing_vs_source = "sampled"
        elif self._fallback_landing_vs is not None:
            self.landing_vs = self._fallback_landing_vs
            self.landing_vs_source = "1s"

        details = self.touchdowns()
        if details:
            self.landing = min(details, key=lambda td: td["vs"])
            if self.landing.get("g") is not None:
                self.landing_g = self.landing["g"]
            times = [td.get("time") for td in details]
            self.bounces = sum(1 for a, b in zip(times, times[1:])
                               if a is not None and b is not None and b - a <= BOUNCE_WINDOW_S)

    def touchdowns(self):
        """Every touchdown this flight, with details - the sampled ones if
        any were caught, else the 1 s fallback ones."""
        return self._sampled_details if self._sampled_details else self._fallback_details

    def _record_track(self, data, now, force=False):
        """A track point [seconds since block start, lat, lon, altitude ft,
        IAS kt, GS kt] every TRACK_INTERVAL_S, and at each event (force)."""
        if self.block_start_time is None or data.get("latitude") is None:
            return
        if not force and self._last_track_time is not None and now - self._last_track_time < TRACK_INTERVAL_S:
            return
        self.track.append([
            int(now - self.block_start_time),
            _round(data.get("latitude"), 5), _round(data.get("longitude"), 5),
            _round(data.get("altitude")), _round(data.get("airspeed_indicated")),
            _round(data.get("ground_velocity")),
        ])
        self._last_track_time = now

    def _log(self, event):
        """Keeps an event for the flight's timeline (the debrief)."""
        self.timeline.append(event)

    _SAVED_FIELDS = (
        "state", "was_airborne", "block_start_time", "g_force_peak",
        "landing_vs", "landing_vs_source", "_sampled_touchdowns", "_fallback_landing_vs",
        "_sampled_details", "_fallback_details", "landing", "bounces",
        "track", "timeline", "_last_track_time",
        "landing_g", "distance_nm", "fuel_at_block_start",
        "aircraft_title", "aircraft_atc_type", "aircraft_atc_model", "aircraft_atc_id",
        "dep_lat", "dep_lon", "_prev",
    )

    def to_state(self):
        """Everything needed to carry on tracking this flight later, as a
        JSON-friendly dict (see core/flight_session.py)."""
        return {name: getattr(self, name) for name in self._SAVED_FIELDS}

    def restore_state(self, saved):
        """Puts back a to_state() dict. The next update() carries on from
        the last saved reading - distance to the new position is added, and
        a landing or block end that happened in the meantime is picked up."""
        self.reset()
        for name in self._SAVED_FIELDS:
            if name in saved:
                setattr(self, name, saved[name])
        self._sampled_touchdowns = list(self._sampled_touchdowns or [])
        self._sampled_details = list(self._sampled_details or [])
        self._fallback_details = list(self._fallback_details or [])
        self.track = list(self.track or [])
        self.timeline = list(self.timeline or [])
        self._prev = dict(self._prev or {})

    def update(self, data, can_start_block=True):
        """
        Process one tick of telemetry.

        can_start_block: whether an engine start may begin a new block
        right now. The owner decides this (e.g. FlightSessionController only
        allows it with a dispatched flight plan, parked at the planned
        origin). While False, a running engine in IDLE is simply ignored;
        as soon as it becomes True with the engine running, the block starts
        on that tick. Has no effect once a block is already in progress.
        Defaults to True, the old "any engine start is a flight" behaviour.

        Returns:
            {
                "state": str,
                "events": [ {type, time, ...detail}, ... ],   # new this tick only
                "live": { distance_nm, fuel_burned, elapsed_hours, g_force }
            }
        `data` may be None (connector not ready yet) - handled safely.
        """
        events = []

        if data is None:
            return self._result(events)

        prev = self._prev
        on_ground = data.get("on_ground")
        engine_running = data.get("engine_running")

        if self.state == "IDLE" and engine_running and can_start_block:
            self.block_start_time = time.time()
            self.was_airborne = False
            self.g_force_peak = None
            self.landing_g = None
            self._reset_landing()
            self._reset_record()
            self.distance_nm = 0.0
            self.fuel_at_block_start = data.get("fuel_total_weight")
            self.aircraft_title = data.get("title")
            self.aircraft_atc_type = data.get("atc_type")
            self.aircraft_atc_model = data.get("atc_model")
            self.aircraft_atc_id = data.get("atc_id")
            self.dep_lat = data.get("latitude")
            self.dep_lon = data.get("longitude")
            self.state = "BLOCK"
            events.append({
                "type": "block_start",
                "time": self.block_start_time,
                "aircraft_title": self.aircraft_title,
                "atc_type": self.aircraft_atc_type,
                "atc_model": self.aircraft_atc_model,
                "atc_id": self.aircraft_atc_id,
                "dep_lat": self.dep_lat,
                "dep_lon": self.dep_lon,
            })
            self._log({"type": "block_start", "time": self.block_start_time,
                       "fuel": _round(self.fuel_at_block_start)})
            self._record_track(data, self.block_start_time, force=True)

        elif self.state == "BLOCK":
            now = time.time()
            if on_ground == 0.0:
                if not self.was_airborne:
                    events.append({"type": "airborne", "time": now})
                if prev.get("on_ground") == 1.0:
                    self._log({"type": "takeoff", "time": now,
                               "ias": _round(data.get("airspeed_indicated"))})
                    self._record_track(data, now, force=True)
                self.was_airborne = True

                g = data.get("g_force")
                if g is not None:
                    if self.g_force_peak is None or g > self.g_force_peak:
                        self.g_force_peak = g

            sampled_now = []
            if self.was_airborne:
                for item in data.get("touchdowns") or []:
                    touchdown = _as_touchdown(item)
                    if touchdown is None:
                        continue
                    touchdown["time"] = now
                    self._sampled_details.append(touchdown)
                    sampled_now.append(touchdown["vs"])
                if sampled_now:
                    self._sampled_touchdowns.extend(sampled_now)
                    self._update_landing_vs()

            prev_on_ground = prev.get("on_ground")
            if self.was_airborne and prev_on_ground == 0.0 and on_ground == 1.0:
                touchdown_vs = _touchdown_vs(prev.get("vertical_speed"), data.get("vertical_speed"))
                if touchdown_vs is not None and (self._fallback_landing_vs is None
                                                 or touchdown_vs < self._fallback_landing_vs):
                    self._fallback_landing_vs = touchdown_vs
                if touchdown_vs is not None:
                    fallback = _touchdown_from_tick(touchdown_vs, data)
                    fallback["time"] = now
                    self._fallback_details.append(fallback)
                self._update_landing_vs()
                vertical_speed = min(sampled_now) if sampled_now else touchdown_vs
                events.append({
                    "type": "landing",
                    "time": now,
                    "vertical_speed": vertical_speed,
                    "g_force": self.landing_g,
                })
                self._log({"type": "touchdown", "time": now,
                           "ias": _round(data.get("airspeed_indicated"))})
                self._record_track(data, now, force=True)

            gear_now = data.get("gear_handle_position")
            gear_prev = prev.get("gear_handle_position")
            if gear_prev is not None and gear_now is not None and gear_now != gear_prev:
                gear_event = {
                    "type": "gear_change",
                    "time": now,
                    "position": gear_now,
                    "airspeed": data.get("airspeed_indicated"),
                    "vle_limit": data.get("design_speed_vle"),
                    "over_limit": (
                        data.get("design_speed_vle") is not None
                        and data.get("airspeed_indicated") is not None
                        and data.get("airspeed_indicated") > data.get("design_speed_vle")
                    ),
                }
                events.append(gear_event)
                self._log({"type": "gear", "time": now, "down": bool(gear_now),
                           "ias": _round(gear_event["airspeed"]),
                           "limit": _round(gear_event["vle_limit"]),
                           "over_limit": bool(gear_event["over_limit"])})

            flaps_now = data.get("flaps_handle_index")
            flaps_prev = prev.get("flaps_handle_index")
            if flaps_prev is not None and flaps_now is not None and flaps_now != flaps_prev:
                events.append({
                    "type": "flaps_change",
                    "time": now,
                    "position": flaps_now,
                    "airspeed": data.get("airspeed_indicated"),
                })
                self._log({"type": "flaps", "time": now, "position": flaps_now,
                           "ias": _round(data.get("airspeed_indicated"))})

            brake_now = data.get("parking_brake")
            brake_prev = prev.get("parking_brake")
            if brake_prev is not None and brake_now is not None and brake_now != brake_prev:
                events.append({
                    "type": "parking_brake_set" if brake_now else "parking_brake_released",
                    "time": now,
                    "ground_velocity": data.get("ground_velocity"),
                })

            lat, lon = data.get("latitude"), data.get("longitude")
            plat, plon = prev.get("latitude"), prev.get("longitude")
            if lat is not None and plat is not None:
                self.distance_nm += _haversine_nm(plat, plon, lat, lon)
            self._record_track(data, now)

            if not engine_running and self.was_airborne:
                self.block_end_time = now
                self.block_hours = (self.block_end_time - self.block_start_time) / 3600
                self.fuel_at_block_end = data.get("fuel_total_weight")
                self.arr_lat = data.get("latitude")
                self.arr_lon = data.get("longitude")
                if self.fuel_at_block_start is not None and self.fuel_at_block_end is not None:
                    self.fuel_burned = self.fuel_at_block_start - self.fuel_at_block_end
                self._log({"type": "block_end", "time": now, "fuel": _round(self.fuel_at_block_end)})
                self._record_track(data, now, force=True)
                events.append({
                    "type": "block_end",
                    "time": self.block_end_time,
                    "block_start_time": self.block_start_time,
                    "block_hours": self.block_hours,
                    "distance_nm": self.distance_nm,
                    "fuel_burned": self.fuel_burned,
                    "landing_vs": self.landing_vs,
                    "landing_vs_source": self.landing_vs_source,
                    "sampled_touchdowns": list(self._sampled_touchdowns),
                    "landing_g": self.landing_g,
                    "landing": self.landing,
                    "touchdowns": self.touchdowns(),
                    "bounces": self.bounces,
                    "fuel_at_block_start": self.fuel_at_block_start,
                    "fuel_at_block_end": self.fuel_at_block_end,
                    "track": list(self.track),
                    "timeline": list(self.timeline),
                    "aircraft_title": self.aircraft_title,
                    "atc_type": self.aircraft_atc_type,
                    "atc_model": self.aircraft_atc_model,
                    "atc_id": self.aircraft_atc_id,
                    "dep_lat": self.dep_lat,
                    "dep_lon": self.dep_lon,
                    "arr_lat": self.arr_lat,
                    "arr_lon": self.arr_lon,
                })
                self.state = "IDLE"

            elif not engine_running and not self.was_airborne:
                events.append({"type": "block_aborted", "time": now})
                self.state = "IDLE"
                self.block_start_time = None

        self._prev = data
        return self._result(events)

    def _result(self, events):
        elapsed_hours = None
        if self.state == "BLOCK" and self.block_start_time is not None:
            elapsed_hours = (time.time() - self.block_start_time) / 3600

        return {
            "state": self.state,
            "events": events,
            "live": {
                "distance_nm": self.distance_nm,
                "fuel_burned_so_far": (
                    self.fuel_at_block_start - self._prev.get("fuel_total_weight")
                    if self.fuel_at_block_start is not None and self._prev.get("fuel_total_weight") is not None
                    else None
                ),
                "elapsed_hours": elapsed_hours,
                "g_force_peak": self.g_force_peak,
            },
        }