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
import urllib.error
import urllib.request

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