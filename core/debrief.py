"""
core/debrief.py

Turns one logged flight (FlightDatabase.get_flight()) into everything the
debrief page shows, ready to display: header, landing grade and details,
map lines, profile series, plan vs actual and the timeline. No Qt - the
page (ui/pages/debrief_page.py) only lays it out.

Flights logged before 1.1 have no track, timeline or touchdown details;
their debrief shows what there is (grade from the landing rate, times,
a straight planned line) and `recorded` is False.

Usage:
    debrief = build_debrief(db.get_flight(flight_id))
"""

from datetime import datetime, timezone

from core.airports import find_airports
from core.landing_grade import grade_flight

KG_PER_LB = 0.45359237


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def fmt_hm(hours):
    if _number(hours) is None:
        return "—"
    total_minutes = round(hours * 60)
    h, m = divmod(abs(total_minutes), 60)
    return f"{'-' if total_minutes < 0 else ''}{h}h {m:02d}m"


def _fmt_minutes(hours):
    total = round(abs(hours) * 60)
    h, m = divmod(total, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _fuel_text(pounds, units):
    if _number(pounds) is None:
        return ""
    value = pounds * KG_PER_LB if units == "kg" else pounds
    return f"Fuel {value:,.0f} {units}"


def _signed(value, fmt):
    if value is None:
        return ""
    if round(value, 6) == 0:
        return "on plan"
    text = fmt(value)
    return text if text.startswith("-") else "+" + text


def fmt_altitude(feet):
    if _number(feet) is None:
        return "—"
    if feet >= 18000:
        return f"FL{round(feet / 100):03d}"
    return f"{feet:,.0f} ft"


def _utc(timestamp):
    if _number(timestamp) is None:
        return "—"
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%H:%MZ")


def _elapsed(seconds):
    if _number(seconds) is None:
        return ""
    minutes = int(seconds // 60)
    return f"+{minutes // 60}:{minutes % 60:02d}"


def _airport(icao, stored=None, lat=None, lon=None):
    """{icao, lat, lon} from a stored plan airport, or given coordinates."""
    if isinstance(stored, dict) and _number(stored.get("lat")) is not None and _number(stored.get("lon")) is not None:
        return {"icao": icao or stored.get("icao"), "lat": stored["lat"], "lon": stored["lon"]}
    if _number(lat) is not None and _number(lon) is not None:
        return {"icao": icao, "lat": lat, "lon": lon}
    return None


def _endpoints(flight, log, plan):
    dep_icao, arr_icao = flight.get("departure_airport"), flight.get("arrival_airport")
    dep = _airport(dep_icao, plan.get("origin") if (plan.get("origin") or {}).get("icao") == dep_icao else None,
                   log.get("dep_lat"), log.get("dep_lon"))
    arr_stored = next((a for a in (plan.get("destination"), plan.get("alternate"))
                       if isinstance(a, dict) and a.get("icao") == arr_icao), None)
    arr = _airport(arr_icao, arr_stored, log.get("arr_lat"), log.get("arr_lon"))
    missing = [code for code, found in ((dep_icao, dep), (arr_icao, arr)) if found is None and code]
    if missing:
        looked_up = find_airports(missing)
        dep = dep or looked_up.get((dep_icao or "").upper())
        arr = arr or looked_up.get((arr_icao or "").upper())
    return dep, arr


def _map(log, plan, dep, arr):
    planned = []
    origin, destination = plan.get("origin"), plan.get("destination")
    for point in [origin] + list(plan.get("navlog") or []) + [destination]:
        if isinstance(point, dict) and _number(point.get("lat")) is not None and _number(point.get("lon")) is not None:
            coords = [point["lon"], point["lat"]]
            if not planned or planned[-1] != coords:
                planned.append(coords)
    if len(planned) < 2 and dep and arr:
        planned = [[dep["lon"], dep["lat"]], [arr["lon"], arr["lat"]]]

    flown = [[p[2], p[1]] for p in log.get("track") or []
             if len(p) >= 3 and _number(p[1]) is not None and _number(p[2]) is not None]
    return {"planned": planned, "flown": flown, "airports": [a for a in (dep, arr) if a]}


def _profile(log):
    altitude, ias, gs = [], [], []
    for p in log.get("track") or []:
        if len(p) < 6 or _number(p[0]) is None:
            continue
        minutes = p[0] / 60
        if _number(p[3]) is not None:
            altitude.append((minutes, p[3]))
        if _number(p[4]) is not None:
            ias.append((minutes, p[4]))
        if _number(p[5]) is not None:
            gs.append((minutes, p[5]))
    return {"altitude": altitude, "ias": ias, "gs": gs}


def _landing_details(log):
    landing = log.get("landing")
    if not isinstance(landing, dict):
        return []
    pitch, ias, gs = _number(landing.get("pitch")), _number(landing.get("ias")), _number(landing.get("gs"))
    speeds = " · ".join(t for t in (f"IAS {ias:,.0f} kt" if ias is not None else "",
                                     f"GS {gs:,.0f} kt" if gs is not None else "") if t)
    source = {"sampled": "20× a second", "1s": "Once a second"}.get(log.get("landing_vs_source"), "—")
    touchdowns = log.get("touchdowns")
    return [
        ("Pitch", f"{pitch:.1f}° {'nose-up' if pitch >= 0 else 'nose-down'}" if pitch is not None else "Not recorded"),
        ("Touchdown speed", speeds or "Not recorded"),
        ("Touchdowns", f"{len(touchdowns)}" if isinstance(touchdowns, list) else "—"),
        ("Measured", source),
    ]


def _plan_vs_actual(flight, log, plan):
    rows = []

    planned_hours, actual_hours = _number(plan.get("est_block_hours")), _number(flight.get("block_hours"))
    diff = actual_hours - planned_hours if planned_hours is not None and actual_hours is not None else None
    rows.append(("Block time", fmt_hm(planned_hours), fmt_hm(actual_hours),
                 _signed(diff, lambda h: ("-" if h < 0 else "") + _fmt_minutes(h))))

    units = plan.get("units") or "lb"
    fuel = plan.get("fuel") or {}
    ramp, landing = _number(fuel.get("plan_ramp")), _number(fuel.get("plan_landing"))
    if ramp is not None and landing is not None:
        planned_burn = ramp - landing
    else:
        parts = [_number(fuel.get("taxi")), _number(fuel.get("enroute_burn"))]
        planned_burn = sum(parts) if all(p is not None for p in parts) else None
    burned_lb = _number(log.get("fuel_burned"))
    burned = burned_lb * KG_PER_LB if burned_lb is not None and units == "kg" else burned_lb
    diff = burned - planned_burn if burned is not None and planned_burn is not None else None
    in_units = lambda v: f"{v:,.0f} {units}"
    rows.append(("Fuel burned", in_units(planned_burn) if planned_burn is not None else "—",
                 in_units(burned) if burned is not None else "—", _signed(diff, in_units)))

    planned_nm, flown_nm = _number(plan.get("distance_nm")), _number(flight.get("distance_nm"))
    diff = flown_nm - planned_nm if planned_nm is not None and flown_nm is not None else None
    in_nm = lambda v: f"{v:,.0f} nm"
    rows.append(("Distance", in_nm(planned_nm) if planned_nm is not None else "—",
                 in_nm(flown_nm) if flown_nm is not None else "—", _signed(diff, in_nm)))

    altitudes = [p[3] for p in log.get("track") or [] if len(p) >= 4 and _number(p[3]) is not None]
    rows.append(("Cruise altitude", fmt_altitude(_number(plan.get("cruise_altitude_ft"))),
                 fmt_altitude(max(altitudes)) if altitudes else "—", ""))
    return rows


def _flaps_text(position):
    if _number(position) is None:
        return "Flaps"
    if isinstance(position, float) and not position.is_integer():
        return f"Flaps {position * 100:.0f}%"
    return f"Flaps {int(position)}"


def _timeline(log, units):
    start = _number(log.get("block_start_time"))
    rows = []
    for event in log.get("timeline") or []:
        kind, t = event.get("type"), _number(event.get("time"))
        ias = _number(event.get("ias"))
        ias_text = f"IAS {ias:,.0f} kt" if ias is not None else ""
        warning = ""
        if kind == "block_start":
            title, detail = "Block out", _fuel_text(event.get("fuel"), units)
        elif kind == "block_end":
            title, detail = "Block in", _fuel_text(event.get("fuel"), units)
        elif kind == "takeoff":
            title, detail = "Takeoff", ias_text
        elif kind == "touchdown":
            title, detail = "Touchdown", ias_text
        elif kind == "gear":
            title, detail = ("Gear down" if event.get("down") else "Gear up"), ias_text
            if event.get("over_limit"):
                limit = _number(event.get("limit"))
                warning = f"Over the gear limit ({limit:,.0f} kt)" if limit is not None else "Over the gear limit"
        elif kind == "flaps":
            title, detail = _flaps_text(event.get("position")), ias_text
        else:
            continue
        rows.append({"time": _utc(t), "elapsed": _elapsed(t - start if t is not None and start is not None else None),
                     "event": title, "detail": detail, "warning": warning})
    return rows


def build_debrief(flight):
    """Everything the debrief page shows, for one get_flight() dict."""
    log = flight.get("log_data") or {}
    plan = log.get("flight_plan") or {}
    accepted = (flight.get("status") or "accepted") == "accepted"
    dep, arr = _endpoints(flight, log, plan)

    date = (flight.get("departed_at") or flight.get("logged_at") or "").replace("T", " ")[:10]
    subtitle = " · ".join(t for t in (
        flight.get("aircraft_designation"), flight.get("aircraft_registration"),
        date, flight.get("network") or "OFFLINE") if t)

    return {
        "id": flight.get("id"),
        "accepted": accepted,
        "reason": flight.get("reject_reason") if not accepted else None,
        "title": f"{flight.get('flight_number') or 'Flight'}   "
                 f"{flight.get('departure_airport') or '—'} → {flight.get('arrival_airport') or '—'}",
        "subtitle": subtitle,
        "grade": grade_flight(log, flight.get("landing_vs")),
        "landing_details": _landing_details(log),
        "recorded": bool(log.get("track")),
        "map": _map(log, plan, dep, arr),
        "profile": _profile(log),
        "plan_vs_actual": _plan_vs_actual(flight, log, plan),
        "timeline": _timeline(log, plan.get("units") or "lb"),
    }