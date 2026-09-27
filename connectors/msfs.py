import math

from SimConnect import SimConnect, AircraftRequests


# Maps normalized key names -> actual SimConnect variable names.
# Keeping this as one dict makes it easy to see everything being pulled,
# and easy to extend later without hunting through code.
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

    "latitude": "PLANE_LATITUDE",
    "longitude": "PLANE_LONGITUDE",

    "fuel_total_weight": "FUEL_TOTAL_QUANTITY_WEIGHT",
    "total_weight": "TOTAL_WEIGHT",

    "title": "TITLE",
    "atc_type": "ATC_TYPE",
    "atc_model": "ATC_MODEL",

    "gear_handle_position": "GEAR_HANDLE_POSITION",
    "flaps_handle_index": "FLAPS_HANDLE_INDEX",
    "parking_brake": "BRAKE_PARKING_POSITION",

    "design_speed_vle": "DESIGN_SPEED_VLE",
}

# Keys whose raw SimConnect values come back as bytes and need decoding.
_STRING_KEYS = {"title", "atc_type", "atc_model"}

# Keys where the python-simconnect wrapper returns radians despite the
# SimConnect variable name implying degrees - a known quirk on angular
# simvars. Convert to degrees rather than trusting the name.
_RADIAN_KEYS = {"heading_true"}


class MSFSConnector:
    def __init__(self):
        self._sm = None
        self._aq = None

    def connect(self):
        """Establish the SimConnect connection. Raises if the sim isn't reachable."""
        self._sm = SimConnect()
        self._aq = AircraftRequests(self._sm, _time=1000)

    def disconnect(self):
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

        # Bail out early if the essentials aren't ready - avoids handing
        # callers a half-populated dict they'd have to null-check anyway.
        if raw["on_ground"] is None or raw["eng1_combustion"] is None:
            return None

        # Clean up string fields (bytes -> str, strip null padding)
        for key in _STRING_KEYS:
            val = raw.get(key)
            if isinstance(val, bytes):
                raw[key] = val.decode("utf-8").strip("\x00").strip()

        # Derived convenience field: is any engine running
        raw["engine_running"] = bool(raw["eng1_combustion"]) or bool(raw["eng2_combustion"])

        # Convert known radian-returning fields to degrees.
        # Also keep the untouched raw value alongside, so we can verify
        # what the sim is actually returning rather than assume.
        for key in _RADIAN_KEYS:
            val = raw.get(key)
            raw[f"{key}_raw"] = val
            if val is not None:
                raw[key] = math.degrees(val) % 360

        return raw