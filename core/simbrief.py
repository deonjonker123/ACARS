"""
core/simbrief.py

Fetches the pilot's latest SimBrief OFP (flight plan) and turns it into a
plain dict the rest of the app can use - no Qt, no database, no sim.

Uses SimBrief's public fetch API with the numeric Pilot ID (Settings page):
    https://www.simbrief.com/api/xml.fetcher.php?userid=<ID>&json=1
The Pilot ID is used rather than the username because it never changes.

Usage:
    plan = fetch_latest_ofp("123456")
    plan["origin"]["icao"]        # "FAOR"
    plan["destination"]["lat"]    # float
    plan["alternate"]             # airport dict, or None if the OFP has none
    plan["registration"]          # "N482TW"
    plan["cargo_kg"]              # always kg, converted if the OFP is in lbs
    plan["ofp"]["html"]           # SimBrief's own OFP, as generated (HTML/<pre>)
    plan["weather"]["origin"]     # {"metar": str, "taf": str} - snapshot at OFP time
    plan["weights"], plan["fuel"] # numbers in plan["units"] ("kg" or "lb")
    plan["navlog"]                # list of waypoint dicts

Every failure (no ID, no internet, unknown ID, malformed OFP) raises
SimBriefError with a message that can be shown to the pilot as-is.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://www.simbrief.com/api/xml.fetcher.php"
TIMEOUT_SECONDS = 15
LBS_TO_KG = 0.45359237


class SimBriefError(Exception):
    """User-readable reason a flight plan couldn't be fetched or used."""

def _text(value):
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _float(value):
    try:
        return float(_text(value))
    except ValueError:
        return None


def _int(value):
    f = _float(value)
    return int(round(f)) if f is not None else None


def _joined(value):
    """A weather/text field that may be a string, {} (empty) or a list of
    strings (e.g. several alternates) -> one string."""
    if isinstance(value, list):
        return "\n".join(t for t in (_text(v) for v in value) if t)
    return _text(value)


def _unwrap(value):
    """A METAR (or list of them, one per alternate) with SimBrief's fixed-width
    line wrapping removed: each report becomes a single line."""
    reports = value if isinstance(value, list) else [value]
    return "\n".join(" ".join(_text(r).split()) for r in reports if _text(r))


def _numbers(section, keys):
    """Picks numeric fields out of an OFP section: {key: float or None}."""
    section = section if isinstance(section, dict) else {}
    return {key: _float(section.get(key)) for key in keys}

WEIGHT_KEYS = ("oew", "pax_count", "bag_count", "pax_weight", "bag_weight", "cargo",
               "payload", "est_zfw", "max_zfw", "est_tow", "max_tow", "est_ldw",
               "max_ldw", "est_ramp")
FUEL_KEYS = ("taxi", "enroute_burn", "contingency", "alternate_burn", "reserve",
             "etops", "extra", "min_takeoff", "plan_takeoff", "plan_ramp",
             "plan_landing", "avg_fuel_flow", "max_tanks")


def _navlog(section):
    """OFP navlog -> list of waypoint dicts (times in seconds, fuel in
    plan units, altitude in ft)."""
    fixes = (section or {}).get("fix") if isinstance(section, dict) else None
    if isinstance(fixes, dict):       # a single fix comes through as a dict, not a list
        fixes = [fixes]
    waypoints = []
    for fix in fixes or []:
        if not isinstance(fix, dict):
            continue
        waypoints.append({
            "ident": _text(fix.get("ident")),
            "name": _text(fix.get("name")),
            "type": _text(fix.get("type")),
            "airway": _text(fix.get("via_airway")),
            "altitude_ft": _int(fix.get("altitude_feet")),
            "wind_dir": _int(fix.get("wind_dir")),
            "wind_spd": _int(fix.get("wind_spd")),
            "distance_nm": _float(fix.get("distance")),
            "time_leg_s": _float(fix.get("time_leg")),
            "time_total_s": _float(fix.get("time_total")),
            "fuel_used": _float(fix.get("fuel_totalused")),
            "fuel_onboard": _float(fix.get("fuel_plan_onboard")),
        })
    return waypoints


def _airport(section):
    """OFP origin/destination/alternate section -> airport dict, or None
    if the section is missing or has no ICAO code."""
    if isinstance(section, list):          # several alternates planned: use the first
        section = section[0] if section else None
    if not isinstance(section, dict):
        return None
    icao = _text(section.get("icao_code")).upper()
    if not icao:
        return None
    return {
        "icao": icao,
        "name": _text(section.get("name")),
        "lat": _float(section.get("pos_lat")),
        "lon": _float(section.get("pos_long")),
    }

def parse_ofp(data):
    """Raw SimBrief JSON (dict) -> flight plan dict. Raises SimBriefError
    if the OFP is missing anything the app can't work without."""
    general = data.get("general") or {}
    params = data.get("params") or {}
    aircraft = data.get("aircraft") or {}
    weights = data.get("weights") or {}
    times = data.get("times") or {}
    atc = data.get("atc") or {}
    text = data.get("text") or {}
    files = data.get("files") or {}
    weather = data.get("weather") or {}

    origin = _airport(data.get("origin"))
    destination = _airport(data.get("destination"))
    if origin is None or destination is None:
        raise SimBriefError("The SimBrief OFP has no origin or destination airport.")
    for ap in (origin, destination):
        if ap["lat"] is None or ap["lon"] is None:
            raise SimBriefError(f"The SimBrief OFP has no coordinates for {ap['icao']}.")

    alternate = _airport(data.get("alternate"))
    if alternate is not None and (alternate["lat"] is None or alternate["lon"] is None):
        alternate = None

    registration = _text(aircraft.get("reg")).upper()

    airline = _text(general.get("icao_airline")).upper()
    number = _text(general.get("flight_number"))
    flight_number = f"{airline}{number}" if number else (_text(atc.get("callsign")) or None)

    units = _text(params.get("units")).lower()
    cargo = _float(weights.get("cargo"))
    if cargo is not None and units.startswith("lb"):
        cargo *= LBS_TO_KG

    est_block_seconds = _float(times.get("est_block"))

    pdf = files.get("pdf") if isinstance(files.get("pdf"), dict) else {}
    pdf_url = None
    if _text(files.get("directory")) and _text(pdf.get("link")):
        pdf_url = _text(files.get("directory")) + _text(pdf.get("link"))

    return {
        "ofp_id": _text(params.get("request_id")) or None,
        "generated_at": _int(params.get("time_generated")),   # unix time
        "flight_number": flight_number,
        "callsign": _text(atc.get("callsign")) or flight_number,
        "origin": origin,
        "destination": destination,
        "alternate": alternate,
        "aircraft_type": _text(aircraft.get("icaocode") or aircraft.get("icao_code")).upper(),
        "aircraft_name": _text(aircraft.get("name")),
        "registration": registration,
        "route": _text(general.get("route")),
        "cruise_altitude_ft": _int(general.get("initial_altitude")),
        "distance_nm": _float(general.get("route_distance")) or _float(general.get("air_distance")),
        "est_block_hours": est_block_seconds / 3600 if est_block_seconds else None,
        "pax_count": _int(weights.get("pax_count")),
        "cargo_kg": round(cargo) if cargo is not None else None,

        # ---- Flight Plan page detail ----
        "units": "lb" if units.startswith("lb") else "kg",
        "ofp": {
            "html": _text(text.get("plan_html")),
            "pdf_url": pdf_url,
            "tlr_text": _text(text.get("tlr_section")),
        },
        "weather": {
            "origin": {"metar": _unwrap(weather.get("orig_metar")), "taf": _joined(weather.get("orig_taf"))},
            "destination": {"metar": _unwrap(weather.get("dest_metar")), "taf": _joined(weather.get("dest_taf"))},
            "alternate": {"metar": _unwrap(weather.get("altn_metar")), "taf": _joined(weather.get("altn_taf"))},
        },
        "weights": _numbers(weights, WEIGHT_KEYS),
        "fuel": _numbers(data.get("fuel"), FUEL_KEYS),
        "navlog": _navlog(data.get("navlog")),
    }

def fetch_latest_ofp(pilot_id):
    """Downloads and parses the latest OFP for this SimBrief Pilot ID."""
    pilot_id = _text(pilot_id)
    if not pilot_id:
        raise SimBriefError("No SimBrief Pilot ID set. Add it on the Settings page.")

    url = f"{API_URL}?{urllib.parse.urlencode({'userid': pilot_id, 'json': 1})}"
    request = urllib.request.Request(url, headers={"User-Agent": "Tailwind-ACARS"})

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        raise SimBriefError(_error_from_body(e.read()) or f"SimBrief returned HTTP {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise SimBriefError(f"Couldn't reach SimBrief ({reason}). Check your internet connection.")

    try:
        data = json.loads(body)
    except ValueError:
        raise SimBriefError("SimBrief sent a response that couldn't be read.")

    status = _text((data.get("fetch_status") or {}).get("status"))
    if status and status.lower() != "success":
        raise SimBriefError(_friendly_status(status))

    return parse_ofp(data)


def _error_from_body(body):
    try:
        status = _text((json.loads(body).get("fetch_status") or {}).get("status"))
    except (ValueError, AttributeError):
        return None
    return _friendly_status(status) if status else None


def _friendly_status(status):
    lowered = status.lower()
    if "unknown userid" in lowered or "unknown username" in lowered:
        return "SimBrief doesn't recognise that Pilot ID. Check it on the Settings page."
    if "no flight plan" in lowered:
        return "No flight plan found on SimBrief. Generate an OFP there first."
    return f"SimBrief: {status}"


if __name__ == "__main__":
    import sys
    from pprint import pprint

    if len(sys.argv) > 1:
        pid = sys.argv[1]
    else:
        from core.db import FlightDatabase
        pid = (FlightDatabase().get_pilot() or {}).get("simbrief_id")

    try:
        plan = fetch_latest_ofp(pid)
    except SimBriefError as e:
        print("SimBriefError:", e)
        sys.exit(1)

    ofp = plan["ofp"]
    navlog = plan["navlog"]
    summary = {k: v for k, v in plan.items() if k not in ("ofp", "navlog")}
    pprint(summary, sort_dicts=False)
    print(f"\nOFP html: {len(ofp['html']):,} characters")
    print(f"OFP pdf:  {ofp['pdf_url']}")
    print(f"TLR:      {'present, ' + format(len(ofp['tlr_text']), ',') + ' characters' if ofp['tlr_text'] else 'not in this OFP'}")
    print(f"Navlog:   {len(navlog)} waypoints")
    for wp in navlog[:3]:
        print("  ", wp)