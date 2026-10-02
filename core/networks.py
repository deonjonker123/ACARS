"""
core/networks.py

Checks whether the pilot is connected to VATSIM or IVAO, using each
network's free public feed of everyone online (no login needed). No Qt,
no database, no sim - the caller passes in the IDs and the sim position.

    VATSIM: https://data.vatsim.net/v3/vatsim-data.json   (pilots[].cid)
    IVAO:   https://api.ivao.aero/v2/tracker/whazzup      (clients.pilots[].userId)

Both feeds refresh about every 15 s and are a few MB, so call this from a
background thread and not more than about once a minute.

A pilot counts as online on a network when their ID is in that network's
pilot list AND the position the network reports is within
MAX_DISTANCE_NM of the sim position (so a stale or unrelated session
doesn't count). If the network hasn't reported a position yet (just
connected), the ID match alone counts.

Usage:
    result = check_online({"VATSIM": "1234567", "IVAO": "654321"}, lat, lon)
    result["network"]     # "VATSIM", "IVAO", or None (offline on both)
    result["callsign"]    # the callsign on that network, or None
    result["errors"]      # {"IVAO": "Couldn't reach IVAO (...)"} - feeds that
                          # couldn't be checked this time (not counted either way)

A blank ID skips that network. A pilot can only be online on one network
at a time, so the first match is the answer.
"""

import gzip
import json
import math
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

FEEDS = {
    "VATSIM": "https://data.vatsim.net/v3/vatsim-data.json",
    "IVAO": "https://api.ivao.aero/v2/tracker/whazzup",
}
TIMEOUT_SECONDS = 15
MAX_DISTANCE_NM = 50.0


class NetworkError(Exception):
    """A network feed couldn't be fetched or read. The message is user-readable."""


def _distance_nm(lat1, lon1, lat2, lon2):
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(min(1.0, math.sqrt(a)))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _same_id(feed_value, user_id):
    return str(feed_value).strip() == str(user_id).strip()


def vatsim_pilot(data, cid):
    """The pilot with this CID in a VATSIM v3 feed, as
    {"callsign", "lat", "lon"}, or None if they're not connected."""
    for pilot in (data or {}).get("pilots") or []:
        if isinstance(pilot, dict) and _same_id(pilot.get("cid"), cid):
            return {
                "callsign": pilot.get("callsign") or None,
                "lat": _number(pilot.get("latitude")),
                "lon": _number(pilot.get("longitude")),
            }
    return None


def ivao_pilot(data, vid):
    """The pilot with this VID in an IVAO whazzup v2 feed, as
    {"callsign", "lat", "lon"}, or None if they're not connected."""
    clients = (data or {}).get("clients") or {}
    for pilot in clients.get("pilots") or []:
        if isinstance(pilot, dict) and _same_id(pilot.get("userId"), vid):
            track = pilot.get("lastTrack") or {}
            return {
                "callsign": pilot.get("callsign") or None,
                "lat": _number(track.get("latitude")),
                "lon": _number(track.get("longitude")),
            }
    return None


_FINDERS = {"VATSIM": vatsim_pilot, "IVAO": ivao_pilot}


def fetch_feed(network):
    """Downloads and parses one network's feed. Raises NetworkError."""
    request = urllib.request.Request(FEEDS[network], headers={
        "User-Agent": "Flyt-ACARS",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        raise NetworkError(f"{network} returned HTTP {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkError(f"Couldn't reach {network} ({getattr(e, 'reason', e)}).")
    try:
        return json.loads(body)
    except ValueError:
        raise NetworkError(f"{network} sent data that couldn't be read.")


def match(network, data, user_id, lat, lon, max_distance_nm=MAX_DISTANCE_NM):
    """The pilot's entry in an already-fetched feed if they count as online
    (ID found and within range of the sim position), else None."""
    pilot = _FINDERS[network](data, user_id)
    if pilot is None:
        return None
    if None in (pilot["lat"], pilot["lon"]) or _number(lat) is None or _number(lon) is None:
        return pilot
    if _distance_nm(lat, lon, pilot["lat"], pilot["lon"]) > max_distance_nm:
        return None
    return pilot


def check_online(ids, lat, lon, fetch=fetch_feed):
    """Checks each network with an ID set. See the module docstring for the
    result. `fetch` can be swapped out for testing."""
    result = {"network": None, "callsign": None, "errors": {}}
    for network in ("VATSIM", "IVAO"):
        user_id = str((ids or {}).get(network) or "").strip()
        if not user_id:
            continue
        try:
            data = fetch(network)
        except NetworkError as e:
            result["errors"][network] = str(e)
            continue
        pilot = match(network, data, user_id, lat, lon)
        if pilot is not None:
            result["network"] = network
            result["callsign"] = pilot["callsign"]
            break
    return result


ATC_POSITIONS = ("FSS", "CTR", "APP", "DEP", "TWR", "GND", "DEL", "ATIS")
WAKE_CATEGORIES = ("L", "M", "H", "J")
_VATSIM_FACILITIES = {1: "FSS", 2: "DEL", 3: "GND", 4: "TWR", 5: "APP", 6: "CTR"}
_VATSIM_INACTIVE_FREQUENCY = "199.998"
_IVAO_ATC_RATINGS = {1: "OBS", 2: "AS1", 3: "AS2", 4: "AS3", 5: "ADC", 6: "APC",
                     7: "ACC", 8: "SEC", 9: "SAI", 10: "CAI"}
_ISO_TIME = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")


def _wake(value):
    """"L", "M", "H" or "J" (light, medium, heavy, super), or "" if unknown."""
    letter = str(value or "").strip().upper()[:1]
    return letter if letter in WAKE_CATEGORIES else ""


def _epoch(value):
    """A feed timestamp ("2026-10-02T15:10:21.0400296Z", always UTC) as Unix
    seconds, or None."""
    match = _ISO_TIME.match(str(value or "").strip())
    if not match:
        return None
    try:
        moment = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).timestamp()


def _atc_position(callsign):
    """"CTR", "APP", "TWR"... from the end of an ATC callsign
    (EGLL_N_APP -> "APP"), or None if it isn't one of ATC_POSITIONS."""
    suffix = str(callsign).rsplit("_", 1)[-1].upper()
    return suffix if suffix in ATC_POSITIONS else None


def _frequency(value):
    number = _number(value)
    return f"{number:.3f}" if number is not None else None


def _text_lines(lines):
    if isinstance(lines, str):
        lines = [lines]
    return [str(line).strip() for line in lines or [] if str(line).strip()]


def _traffic_entry(user_id, callsign, name, lat, lon, heading, altitude,
                   groundspeed, aircraft, wake, dep, arr):
    return {
        "id": str(user_id or ""),
        "callsign": str(callsign or ""),
        "name": str(name or ""),
        "lat": lat,
        "lon": lon,
        "heading": _number(heading) or 0,
        "altitude": _number(altitude),
        "groundspeed": _number(groundspeed),
        "aircraft": str(aircraft or ""),
        "wake": _wake(wake),
        "dep": str(dep or ""),
        "arr": str(arr or ""),
    }


def vatsim_traffic(data):
    """Every pilot with a position in a VATSIM v3 feed, as traffic entries
    (see network_snapshot)."""
    traffic = []
    for pilot in (data or {}).get("pilots") or []:
        if not isinstance(pilot, dict):
            continue
        lat, lon = _number(pilot.get("latitude")), _number(pilot.get("longitude"))
        if lat is None or lon is None:
            continue
        plan = pilot.get("flight_plan") or {}
        icao_aircraft = str(plan.get("aircraft") or "").split("/")
        traffic.append(_traffic_entry(
            pilot.get("cid"), pilot.get("callsign"), pilot.get("name"), lat, lon,
            pilot.get("heading"), pilot.get("altitude"), pilot.get("groundspeed"),
            plan.get("aircraft_short"), icao_aircraft[1] if len(icao_aircraft) > 1 else "",
            plan.get("departure"), plan.get("arrival"),
        ))
    return traffic


def ivao_traffic(data):
    """Every pilot with a position in an IVAO whazzup v2 feed, as traffic
    entries (see network_snapshot). IVAO doesn't give names."""
    traffic = []
    clients = (data or {}).get("clients") or {}
    for pilot in clients.get("pilots") or []:
        if not isinstance(pilot, dict):
            continue
        track = pilot.get("lastTrack") or {}
        lat, lon = _number(track.get("latitude")), _number(track.get("longitude"))
        if lat is None or lon is None:
            continue
        plan = pilot.get("flightPlan") or {}
        traffic.append(_traffic_entry(
            pilot.get("userId"), pilot.get("callsign"), None, lat, lon,
            track.get("heading"), track.get("altitude"), track.get("groundSpeed"),
            plan.get("aircraftId"), (plan.get("aircraft") or {}).get("wakeTurbulence"),
            plan.get("departureId"), plan.get("arrivalId"),
        ))
    return traffic


def vatsim_controllers(data):
    """Every working controller and ATIS in a VATSIM v3 feed, as controller
    entries (see network_snapshot). Observers, supervisors and positions
    on the 199.998 "not primary" frequency are left out. VATSIM gives
    controllers no position, so lat/lon are None."""
    data = data or {}
    ratings = {r.get("id"): r.get("short") for r in data.get("ratings") or [] if isinstance(r, dict)}
    entries = ([(c, None) for c in data.get("controllers") or []]
               + [(a, "ATIS") for a in data.get("atis") or []])
    controllers = []
    for entry, position in entries:
        if not isinstance(entry, dict):
            continue
        callsign = str(entry.get("callsign") or "").strip().upper()
        position = position or _atc_position(callsign) or _VATSIM_FACILITIES.get(entry.get("facility"))
        frequency = _frequency(entry.get("frequency"))
        if not callsign or position is None or frequency in (None, _VATSIM_INACTIVE_FREQUENCY):
            continue
        controllers.append({
            "callsign": callsign,
            "position": position,
            "frequency": frequency,
            "name": str(entry.get("name") or ""),
            "atis": _text_lines(entry.get("text_atis")),
            "rating": str(ratings.get(entry.get("rating")) or ""),
            "logon": _epoch(entry.get("logon_time")),
            "lat": None,
            "lon": None,
        })
    return controllers


def ivao_controllers(data):
    """Every controller in an IVAO whazzup v2 feed, as controller entries
    (see network_snapshot), with the position IVAO reports for them. The
    voice server line IVAO puts in the ATIS is left out."""
    controllers = []
    clients = (data or {}).get("clients") or {}
    for atc in clients.get("atcs") or []:
        if not isinstance(atc, dict):
            continue
        callsign = str(atc.get("callsign") or "").strip().upper()
        session = atc.get("atcSession") or {}
        position = _atc_position(callsign) or _atc_position(session.get("position") or "")
        frequency = _frequency(session.get("frequency"))
        if not callsign or position is None or frequency is None:
            continue
        track = atc.get("lastTrack") or {}
        lines = (atc.get("atis") or {}).get("lines")
        controllers.append({
            "callsign": callsign,
            "position": position,
            "frequency": frequency,
            "name": "",
            "atis": [line for line in _text_lines(lines) if ".ivao.aero/" not in line],
            "rating": _IVAO_ATC_RATINGS.get(atc.get("rating"), ""),
            "logon": _epoch(atc.get("createdAt")),
            "lat": _number(track.get("latitude")),
            "lon": _number(track.get("longitude")),
        })
    return controllers


_SNAPSHOT_PARSERS = {
    "VATSIM": (vatsim_traffic, vatsim_controllers),
    "IVAO": (ivao_traffic, ivao_controllers),
}


def network_snapshot(network, data):
    """Everyone online in an already-fetched feed, for the Live Map:
        {"network": "VATSIM" or "IVAO",
         "traffic": [{"id", "callsign", "name", "lat", "lon", "heading",
                      "altitude", "groundspeed", "aircraft", "wake", "dep", "arr"}],
         "controllers": [{"callsign", "position", "frequency", "name",
                          "atis": [lines], "rating", "logon", "lat", "lon"}]}
    "position" is one of ATC_POSITIONS. "frequency" is a string like
    "128.325". "wake" is one of WAKE_CATEGORIES or "". "rating" is the
    network's short name ("C1", "ADC"...) or "". "logon" is when they
    connected, in Unix seconds, or None. Missing text is "", missing
    numbers None."""
    traffic, controllers = _SNAPSHOT_PARSERS[network]
    return {"network": network, "traffic": traffic(data), "controllers": controllers(data)}


def fetch_snapshot(network, fetch=fetch_feed):
    """Downloads one network's feed and returns network_snapshot() of it.
    Raises NetworkError."""
    return network_snapshot(network, fetch(network))


if __name__ == "__main__":
    import sys
    from core.db import FlightDatabase

    pilot = FlightDatabase().get_pilot() or {}
    ids = {"VATSIM": pilot.get("vatsim_id"), "IVAO": pilot.get("ivao_id")}
    lat = float(sys.argv[1]) if len(sys.argv) > 2 else None
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else None
    print(f"IDs: {ids}   sim position: {lat}, {lon}")

    for network in ("VATSIM", "IVAO"):
        if not ids[network]:
            print(f"{network}: no ID set - skipped")
            continue
        try:
            data = fetch_feed(network)
        except NetworkError as e:
            print(f"{network}: {e}")
            continue
        found = _FINDERS[network](data, ids[network])
        counted = match(network, data, ids[network], lat, lon)
        print(f"{network}: in feed = {found}   counts as online = {counted is not None}")

    print("check_online ->", check_online(ids, lat, lon))

    for network in ("VATSIM", "IVAO"):
        try:
            snapshot = fetch_snapshot(network)
        except NetworkError as e:
            print(f"{network} snapshot: {e}")
            continue
        by_position = {}
        for controller in snapshot["controllers"]:
            by_position[controller["position"]] = by_position.get(controller["position"], 0) + 1
        print(f"{network} snapshot: {len(snapshot['traffic'])} pilots, ATC {by_position}")