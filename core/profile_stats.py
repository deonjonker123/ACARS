"""
core/profile_stats.py

The Pilot Profile page's numbers, worked out from the logbook. No Qt -
ui/pages/profile_page.py only draws them.

Only ACCEPTED flights count toward the stats; rejected flights only
appear as the rejected count and rate.

Usage:
    stats = build_profile(db.list_flights(limit=None), db.get_pilot())
"""

import csv
import os
from collections import Counter
from datetime import date

from core.airports import _DEFAULT_CSV_PATH, _VALID_TYPES, _row_icao
from core.landing_grade import letter_for, LETTERS

DURATION_BUCKETS = [(0, 2, "0–2 h"), (2, 4, "2–4 h"), (4, 8, "4–8 h"), (8, 16, "8–16 h"), (16, None, "16+ h")]
LANDING_BUCKETS = [(0, 100), (100, 200), (200, 300), (300, 400), (400, 600), (600, 800), (800, None)]
CONTINENTS = {"AF": "Africa", "AN": "Antarctica", "AS": "Asia", "EU": "Europe",
              "NA": "North America", "OC": "Oceania", "SA": "South America"}
TOP_AIRCRAFT = 8
TOP_AIRPORTS = 10
TREND_FLIGHTS = 20
ACTIVITY_MONTHS = 12


def airport_regions(icaos, csv_path=_DEFAULT_CSV_PATH):
    """{ICAO: (country code, continent code)} for the codes found, in one
    pass over airports.csv. {} if the file isn't there."""
    wanted = {str(c).strip().upper() for c in icaos or () if c}
    if not wanted or not os.path.exists(csv_path):
        return {}
    found = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("type") not in _VALID_TYPES:
                continue
            icao = _row_icao(row).upper()
            if icao in wanted and icao not in found:
                found[icao] = (row.get("iso_country") or "", row.get("continent") or "")
                if len(found) == len(wanted):
                    break
    return found


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _count(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _route(f):
    return f"{f.get('flight_number') or 'Flight'}  {f.get('departure_airport') or '—'} → {f.get('arrival_airport') or '—'}"


def _month(f):
    text = (f.get("departed_at") or f.get("logged_at") or "")[:7]
    return text if len(text) == 7 and text[4] == "-" else None


def build_profile(flights, pilot=None, regions=None, today=None):
    """Everything the profile page shows. `flights` from list_flights()
    (newest first); `regions` from airport_regions() - looked up here if
    not given. `today` is for tests."""
    pilot = pilot or {}
    accepted = [f for f in flights if f.get("status") != "rejected"]
    rejected = len(flights) - len(accepted)

    hours = sum(_number(f.get("block_hours")) or 0 for f in accepted)
    totals = {
        "flights": len(accepted),
        "hours": hours,
        "distance_nm": sum(_number(f.get("distance_nm")) or 0 for f in accepted),
        "pax": sum(_number(f.get("pax_count")) or 0 for f in accepted),
        "cargo_kg": sum(_number(f.get("cargo_kg")) or 0 for f in accepted),
        "rejected": rejected,
        "rejection_rate": rejected / len(flights) if flights else None,
    }

    type_hours, type_flights, airframes = Counter(), Counter(), Counter()
    for f in accepted:
        name = f.get("aircraft_designation") or "—"
        type_hours[name] += _number(f.get("block_hours")) or 0
        type_flights[name] += 1
        airframes[f.get("aircraft_registration") or "—"] += 1
    aircraft = {
        "hours_by_type": [{"label": t, "value": h, "tooltip": f"{t}: {h:,.1f} h · {_count(type_flights[t], 'flight')}"}
                          for t, h in type_hours.most_common(TOP_AIRCRAFT)],
        "favourite_type": type_hours.most_common(1)[0][0] if type_hours else None,
        "top_airframe": airframes.most_common(1)[0] if airframes else None,
        "types_flown": len(type_hours),
    }

    visits = Counter()
    for f in accepted:
        for icao in (f.get("departure_airport"), f.get("arrival_airport")):
            if icao and icao != "----":
                visits[icao.upper()] += 1
    if regions is None:
        regions = airport_regions(visits)
    countries = {regions[i][0] for i in visits if i in regions and regions[i][0]}
    continents = {regions[i][1] for i in visits if i in regions and regions[i][1]}
    airports = {
        "top": [{"label": icao, "value": n, "tooltip": f"{icao}: {_count(n, 'visit')}"}
                for icao, n in visits.most_common(TOP_AIRPORTS)],
        "unique": len(visits),
        "countries": len(countries),
        "continents": sorted(CONTINENTS.get(c, c) for c in continents),
    }

    rates = [f["landing_vs"] for f in accepted if _number(f.get("landing_vs")) is not None]
    histogram = []
    for low, high in LANDING_BUCKETS:
        label = f"{low}–{high}" if high else f"{low}+"
        count = sum(1 for r in rates if abs(r) >= low and (high is None or abs(r) < high))
        histogram.append({"label": label, "value": count, "tooltip": f"-{label} fpm: {_count(count, 'landing')}"})
    grades = Counter(letter_for(f.get("landing_grade")) for f in accepted if f.get("landing_grade") is not None)
    scores = [f["landing_grade"] for f in accepted if f.get("landing_grade") is not None]
    trend = [f for f in accepted if _number(f.get("landing_vs")) is not None][:TREND_FLIGHTS][::-1]
    landings = {
        "average": sum(rates) / len(rates) if rates else None,
        "softest": max(rates) if rates else None,
        "hardest": min(rates) if rates else None,
        "average_grade": letter_for(sum(scores) / len(scores)) if scores else None,
        "histogram": histogram,
        "grades": [{"label": letter, "value": grades.get(letter, 0), "letter": letter}
                   for _, letter in LETTERS],
        "trend": [(i + 1, f["landing_vs"]) for i, f in enumerate(trend)],
        "trend_labels": [_route(f) for f in trend],
    }

    durations = []
    for low, high, label in DURATION_BUCKETS:
        count = sum(1 for f in accepted
                    if (_number(f.get("block_hours")) or 0) >= low
                    and (high is None or (_number(f.get("block_hours")) or 0) < high))
        durations.append({"label": label, "value": count, "tooltip": f"{label}: {_count(count, 'flight')}"})

    def best(key, pick=max):
        rows = [f for f in accepted if _number(f.get(key)) is not None]
        return pick(rows, key=lambda f: f[key]) if rows else None

    longest_nm, longest_h, softest = best("distance_nm"), best("block_hours"), best("landing_vs")
    records = []
    if longest_nm:
        records.append(("Longest by distance", f"{longest_nm['distance_nm']:,.0f} nm", _route(longest_nm), longest_nm["id"]))
    if longest_h:
        h = longest_h["block_hours"]
        records.append(("Longest by time", f"{int(h)}h {round(h % 1 * 60):02d}m", _route(longest_h), longest_h["id"]))
    if softest:
        records.append(("Softest landing", f"{softest['landing_vs']:,.0f} fpm", _route(softest), softest["id"]))

    networks = Counter((f.get("network") or "OFFLINE") for f in accepted)
    network_rows = [{"label": n.title() if n == "OFFLINE" else n, "value": networks.get(n, 0),
                     "tooltip": f"{n}: {_count(networks.get(n, 0), 'flight')}"} for n in ("OFFLINE", "VATSIM", "IVAO")]

    today = today or date.today()
    months = []
    year, month = today.year, today.month
    for _ in range(ACTIVITY_MONTHS):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    per_month = Counter(_month(f) for f in accepted)
    activity = [{"label": date(int(m[:4]), int(m[5:]), 1).strftime("%b"), "value": per_month.get(m, 0),
                 "tooltip": f"{date(int(m[:4]), int(m[5:]), 1):%B %Y}: {_count(per_month.get(m, 0), 'flight')}"}
                for m in reversed(months)]

    return {"totals": totals, "aircraft": aircraft, "airports": airports, "landings": landings,
            "durations": durations, "records": records, "networks": network_rows, "activity": activity,
            "pilot_name": pilot.get("name")}