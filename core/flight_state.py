import time
import math


def _haversine_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in nautical miles."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    r_nm = 3440.065  # Earth radius in nautical miles
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
        self.state = "IDLE"  # IDLE -> BLOCK -> IDLE (on completion)
        self.was_airborne = False

        self.block_start_time = None
        self.block_end_time = None
        self.block_hours = None

        # Previous-tick values, used to detect transitions/events
        self._prev = {}

        # Landing quality capture
        self.g_force_peak = None
        self.landing_vs = None
        self.landing_g = None

        # Distance tracking
        self.distance_nm = 0.0

        # Fuel tracking
        self.fuel_at_block_start = None
        self.fuel_at_block_end = None
        self.fuel_burned = None

        # Aircraft identity captured at block start
        self.aircraft_title = None
        self.aircraft_atc_type = None
        self.aircraft_atc_model = None

    def update(self, data):
        """
        Process one tick of telemetry. Returns:
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

        # ---------------- Block start ----------------
        if self.state == "IDLE" and engine_running:
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
            self.state = "BLOCK"
            events.append({
                "type": "block_start",
                "time": self.block_start_time,
                "aircraft_title": self.aircraft_title,
                "atc_type": self.aircraft_atc_type,
                "atc_model": self.aircraft_atc_model,
            })

        elif self.state == "BLOCK":
            # ---- Airborne tracking ----
            if on_ground == 0.0:
                if not self.was_airborne:
                    events.append({"type": "airborne", "time": time.time()})
                self.was_airborne = True

                # Track peak G while airborne (landing bump shows up here)
                g = data.get("g_force")
                if g is not None:
                    if self.g_force_peak is None or g > self.g_force_peak:
                        self.g_force_peak = g

            # ---- Touchdown detection (on_ground transition False -> True) ----
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

            # ---- Gear/flap change events ----
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
                    # No verified VFE simvar available - logged without a limit check.
                })

            # ---- Parking brake changes (taxi timing) ----
            brake_now = data.get("parking_brake")
            brake_prev = prev.get("parking_brake")
            if brake_prev is not None and brake_now is not None and brake_now != brake_prev:
                events.append({
                    "type": "parking_brake_set" if brake_now else "parking_brake_released",
                    "time": time.time(),
                    "ground_velocity": data.get("ground_velocity"),
                })

            # ---- Distance accumulation ----
            lat, lon = data.get("latitude"), data.get("longitude")
            plat, plon = prev.get("latitude"), prev.get("longitude")
            if lat is not None and plat is not None:
                self.distance_nm += _haversine_nm(plat, plon, lat, lon)

            # ---- Block end ----
            if not engine_running and self.was_airborne:
                self.block_end_time = time.time()
                self.block_hours = (self.block_end_time - self.block_start_time) / 3600
                self.fuel_at_block_end = data.get("fuel_total_weight")
                if self.fuel_at_block_start is not None and self.fuel_at_block_end is not None:
                    self.fuel_burned = self.fuel_at_block_start - self.fuel_at_block_end
                events.append({
                    "type": "block_end",
                    "time": self.block_end_time,
                    "block_hours": self.block_hours,
                    "distance_nm": self.distance_nm,
                    "fuel_burned": self.fuel_burned,
                    "landing_vs": self.landing_vs,
                    "landing_g": self.landing_g,
                })
                self.state = "IDLE"

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