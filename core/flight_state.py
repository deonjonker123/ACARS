"""
core/flight_state.py

Takes normalized telemetry dicts (as produced by any connector, e.g.
connectors.msfs.MSFSConnector.read()) one tick at a time, and turns them
into flight-level facts: block start/end, distance flown, fuel burned,
landing quality, gear/flap speed checks, and taxi timing.

This module knows nothing about SimConnect, UDP, or any specific sim -
it only understands the normalized dict shape. That's what lets the same
tracker serve MSFS, P3D, and X-Plane connectors later.

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
        self.landing_vs = None
        self.landing_g = None
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
            self.landing_vs = None
            self.landing_g = None
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

        elif self.state == "BLOCK":
            if on_ground == 0.0:
                if not self.was_airborne:
                    events.append({"type": "airborne", "time": time.time()})
                self.was_airborne = True

                g = data.get("g_force")
                if g is not None:
                    if self.g_force_peak is None or g > self.g_force_peak:
                        self.g_force_peak = g

            prev_on_ground = prev.get("on_ground")
            if self.was_airborne and prev_on_ground == 0.0 and on_ground == 1.0:
                self.landing_vs = data.get("vertical_speed")
                self.landing_g = self.g_force_peak
                events.append({
                    "type": "landing",
                    "time": time.time(),
                    "vertical_speed": self.landing_vs,
                    "g_force": self.landing_g,
                })

            gear_now = data.get("gear_handle_position")
            gear_prev = prev.get("gear_handle_position")
            if gear_prev is not None and gear_now is not None and gear_now != gear_prev:
                events.append({
                    "type": "gear_change",
                    "time": time.time(),
                    "position": gear_now,
                    "airspeed": data.get("airspeed_indicated"),
                    "vle_limit": data.get("design_speed_vle"),
                    "over_limit": (
                        data.get("design_speed_vle") is not None
                        and data.get("airspeed_indicated") is not None
                        and data.get("airspeed_indicated") > data.get("design_speed_vle")
                    ),
                })

            flaps_now = data.get("flaps_handle_index")
            flaps_prev = prev.get("flaps_handle_index")
            if flaps_prev is not None and flaps_now is not None and flaps_now != flaps_prev:
                events.append({
                    "type": "flaps_change",
                    "time": time.time(),
                    "position": flaps_now,
                    "airspeed": data.get("airspeed_indicated"),
                })

            brake_now = data.get("parking_brake")
            brake_prev = prev.get("parking_brake")
            if brake_prev is not None and brake_now is not None and brake_now != brake_prev:
                events.append({
                    "type": "parking_brake_set" if brake_now else "parking_brake_released",
                    "time": time.time(),
                    "ground_velocity": data.get("ground_velocity"),
                })

            lat, lon = data.get("latitude"), data.get("longitude")
            plat, plon = prev.get("latitude"), prev.get("longitude")
            if lat is not None and plat is not None:
                self.distance_nm += _haversine_nm(plat, plon, lat, lon)

            if not engine_running and self.was_airborne:
                self.block_end_time = time.time()
                self.block_hours = (self.block_end_time - self.block_start_time) / 3600
                self.fuel_at_block_end = data.get("fuel_total_weight")
                self.arr_lat = data.get("latitude")
                self.arr_lon = data.get("longitude")
                if self.fuel_at_block_start is not None and self.fuel_at_block_end is not None:
                    self.fuel_burned = self.fuel_at_block_start - self.fuel_at_block_end
                events.append({
                    "type": "block_end",
                    "time": self.block_end_time,
                    "block_start_time": self.block_start_time,
                    "block_hours": self.block_hours,
                    "distance_nm": self.distance_nm,
                    "fuel_burned": self.fuel_burned,
                    "landing_vs": self.landing_vs,
                    "landing_g": self.landing_g,
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
                events.append({"type": "block_aborted", "time": time.time()})
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