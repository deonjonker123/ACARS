"""
MSFS / P3D connector (SimConnect-based).

This module is a pure data layer: it knows how to talk to SimConnect and
return a normalized dict of current sim state. It does NOT log or make any
decisions about flights/events - that's core/flight_state.py's job. (The
only print is a one-off line if the touchdown sampler fails.)

Usage:
    connector = MSFSConnector()
    connector.connect()
    data = connector.read()   # -> dict, or None if not ready
    connector.disconnect()

Touchdown rate: read() is only called about once a second, too coarse to
catch the vertical speed at the moment of touchdown. So while connected, a
small background thread (_TouchdownSampler) reads just on-ground and
vertical speed every SAMPLE_INTERVAL_S (20x a second) through its own,
uncached SimConnect requests. Each airborne -> on-ground change it sees is
a touchdown, recorded as the more negative of the last airborne and first
on-ground vertical speed. read() hands them over in data["touchdowns"]
(the touchdown rates since the previous read(), usually an empty list).
"""

import math
import re
import threading
import time

from SimConnect import SimConnect, AircraftRequests
from connectors.simconnect_probe import SimConnectProbe


SIMVARS = {
    "on_ground": "SIM_ON_GROUND",
    "eng1_combustion": "GENERAL_ENG_COMBUSTION:1",
    "eng2_combustion": "GENERAL_ENG_COMBUSTION:2",

    "altitude": "PLANE_ALTITUDE",
    "alt_above_ground": "PLANE_ALT_ABOVE_GROUND",
    "airspeed_indicated": "AIRSPEED_INDICATED",
    "ground_velocity": "GROUND_VELOCITY",
    "vertical_speed": "VERTICAL_SPEED",
    "g_force": "G_FORCE",
    "heading_true": "PLANE_HEADING_DEGREES_MAGNETIC",
    "bank": "PLANE_BANK_DEGREES",
    "pitch": "PLANE_PITCH_DEGREES",

    "latitude": "PLANE_LATITUDE",
    "longitude": "PLANE_LONGITUDE",

    "fuel_total_weight": "FUEL_TOTAL_QUANTITY_WEIGHT",
    "total_weight": "TOTAL_WEIGHT",

    "title": "TITLE",
    "atc_type": "ATC_TYPE",
    "atc_model": "ATC_MODEL",
    "atc_id": "ATC_ID",

    "gear_handle_position": "GEAR_HANDLE_POSITION",
    "flaps_handle_index": "FLAPS_HANDLE_INDEX",
    "parking_brake": "BRAKE_PARKING_POSITION",

    "design_speed_vle": "DESIGN_SPEED_VLE",
}

_STRING_KEYS = {"title", "atc_type", "atc_model", "atc_id"}
_ATCCOM_KEYS = {"atc_type", "atc_model"}
_ATCCOM_PATTERN = re.compile(r'ATCCOM\.(?:ATC_NAME|AC_MODEL)\s+(.+?)\.\d+\.text')


def _sanitize_atccom(raw):
    if raw is None:
        return None
    match = _ATCCOM_PATTERN.match(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()

_RADIAN_KEYS = {"heading_true"}

SAMPLE_INTERVAL_S = 0.05
TOUCHDOWN_G_SAMPLES = 10
MIN_AIRBORNE_S = 1.0

def _degrees(radians):
    return math.degrees(radians) if radians is not None else None


def _attitude(bank_rad, pitch_rad):
    """SimConnect bank/pitch (radians) -> (bank, pitch) in degrees: bank as
    an angle either way (0 = wings level), pitch positive nose-up
    (SimConnect's own pitch is negative nose-up)."""
    bank, pitch = _degrees(bank_rad), _degrees(pitch_rad)
    return (abs(bank) if bank is not None else None,
            -pitch if pitch is not None else None)


class _TouchdownSampler(threading.Thread):
    """Reads on-ground + vertical speed 20x a second and records every
    touchdown: its vertical speed, the attitude and speeds at that moment,
    and the peak G over the next TOUCHDOWN_G_SAMPLES samples (half a
    second - the G spike comes as the gear takes the weight). See the
    module docstring."""

    def __init__(self, sm):
        super().__init__(name="TouchdownSampler", daemon=True)
        self._aq = AircraftRequests(sm, _time=0)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._touchdowns = []
        self._open = None
        self._open_samples_left = 0
        self._was_on_ground = None
        self._airborne_since = None
        self._last_airborne_vs = None
        self._error_reported = False

    def run(self):
        while not self._stop_event.is_set():
            try:
                self._sample()
            except Exception as e:
                if not self._error_reported:
                    print(f"[msfs] Touchdown sampler error (falling back to 1 s readings): {e}")
                    self._error_reported = True
            self._stop_event.wait(SAMPLE_INTERVAL_S)

    def _sample(self):
        on_ground = self._aq.get("SIM_ON_GROUND")
        vs = self._aq.get("VERTICAL_SPEED")
        if on_ground is None:
            return
        on_ground = on_ground >= 0.5

        if self._open is not None:
            self._track_touchdown_g()

        now = time.monotonic()
        if not on_ground:
            if self._was_on_ground is not False:
                self._airborne_since = now
            if vs is not None:
                self._last_airborne_vs = vs
        elif (self._was_on_ground is False and self._last_airborne_vs is not None
              and self._airborne_since is not None and now - self._airborne_since >= MIN_AIRBORNE_S):
            readings = [self._last_airborne_vs] + ([vs] if vs is not None else [])
            self._open_touchdown(min(readings))
        self._was_on_ground = on_ground

    def _open_touchdown(self, vs):
        """A touchdown just happened: note the moment's attitude and speeds,
        then keep watching G for a few samples before handing it over."""
        if self._open is not None:
            self._close_touchdown()
        bank, pitch = _attitude(self._aq.get("PLANE_BANK_DEGREES"), self._aq.get("PLANE_PITCH_DEGREES"))
        self._open = {
            "vs": vs,
            "g": self._aq.get("G_FORCE"),
            "bank": bank,
            "pitch": pitch,
            "ias": self._aq.get("AIRSPEED_INDICATED"),
            "gs": self._aq.get("GROUND_VELOCITY"),
        }
        self._open_samples_left = TOUCHDOWN_G_SAMPLES

    def _track_touchdown_g(self):
        g = self._aq.get("G_FORCE")
        if g is not None and (self._open["g"] is None or g > self._open["g"]):
            self._open["g"] = g
        self._open_samples_left -= 1
        if self._open_samples_left <= 0:
            self._close_touchdown()

    def _close_touchdown(self):
        with self._lock:
            self._touchdowns.append(self._open)
        self._open = None

    def take(self):
        """The touchdowns recorded since the last call (clears them), each
        {"vs", "g", "bank", "pitch", "ias", "gs"} - fpm, G, degrees, kt."""
        with self._lock:
            touchdowns, self._touchdowns = self._touchdowns, []
        return touchdowns

    def stop(self):
        self._stop_event.set()

class MSFSConnector:
    SIM_NAME = "MSFS / P3D (SimConnect)"

    def __init__(self):
        self._sm = None
        self._aq = None
        self._sampler = None
        self._probe = SimConnectProbe()

    @property
    def simconnect_check(self):
        """The latest SimConnect check result - see connectors/simconnect_probe.py."""
        return self._probe.last_result

    def connect(self):
        """Establish the SimConnect connection. Raises if the sim isn't reachable."""
        if not self._probe.check():
            raise ConnectionError(self._probe.status)
        self._sm = SimConnect()
        self._aq = AircraftRequests(self._sm, _time=1000)
        self._sampler = _TouchdownSampler(self._sm)
        self._sampler.start()

    def disconnect(self):
        if self._sampler:
            self._sampler.stop()
            self._sampler.join(timeout=1.0)
            self._sampler = None
        if self._sm:
            self._sm.exit()
            self._sm = None
            self._aq = None

    def is_connected(self):
        return self._sm is not None

    def read(self):
        """
        Read all tracked simvars in one pass.

        Returns a dict of {normalized_key: value}, or None if core values
        aren't ready yet (e.g. called immediately after connect()).
        """
        if self._aq is None:
            return None

        raw = {}
        for key, simvar in SIMVARS.items():
            raw[key] = self._aq.get(simvar)

        if raw["on_ground"] is None or raw["eng1_combustion"] is None:
            return None

        for key in _STRING_KEYS:
            val = raw.get(key)
            if isinstance(val, bytes):
                raw[key] = val.decode("utf-8").strip("\x00").strip()

        for key in _ATCCOM_KEYS:
            raw[key] = _sanitize_atccom(raw.get(key))

        raw["engine_running"] = bool(raw["eng1_combustion"]) or bool(raw["eng2_combustion"])

        for key in _RADIAN_KEYS:
            val = raw.get(key)
            raw[f"{key}_raw"] = val
            if val is not None:
                raw[key] = math.degrees(val) % 360

        raw["bank"], raw["pitch"] = _attitude(raw.get("bank"), raw.get("pitch"))
        raw["touchdowns"] = self._sampler.take() if self._sampler else []

        return raw