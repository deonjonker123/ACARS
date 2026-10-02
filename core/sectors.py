"""
core/sectors.py

Where each online controller goes on the Live Map: centre and FSS
positions get their FIR / UIR outline, approach and departure their
TRACON outline, and tower, ground, delivery and ATIS a marker at their
airport. Used for both VATSIM and IVAO - IVAO callsigns use the same
ICAO codes. No Qt; the caller passes in core.networks controllers.

The outlines come from two community projects VATSIM's own maps use:
    VATSpy Data Project     VATSpy.dat (FIRs, UIRs, airports, callsign
                            prefixes) + Boundaries.geojson (FIR outlines)
    SimAware TRACON Project TRACONBoundaries.geojson (approach outlines)

They change a few times a year, so a copy is kept in USER_DATA_DIR/sectors
and refreshed when it's older than MAX_AGE_DAYS. If that can't be done
(offline, GitHub down, a broken download), the last good copy is used, and
failing that the one bundled with the app in data/. A download only
replaces the cached copy once it has been read successfully.

Both calls are slow (downloads, a few MB of JSON) - run them on a
background thread, once per session:
    refresh_cache()                  # {"VATSpy.dat": "updated", ...}
    sectors = load_sector_data()     # raises SectorDataError if unusable
    placed = sectors.place(snapshot["controllers"])
    placed["sectors"]   # [{"id", "kind" ("fir"/"uir"/"tracon"), "name",
                        #   "geometry" (GeoJSON), "label": [lon, lat],
                        #   "controllers": [...]}]
    placed["stations"]  # [{"icao", "name", "lat", "lon", "controllers": [...]}]
    placed["unplaced"]  # controllers that couldn't be put anywhere

Run this file directly to refresh the cache and see how the controllers
online right now get placed:
    python -m core.sectors
"""

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.paths import RESOURCE_DIR, USER_DATA_DIR

SOURCES = {
    "VATSpy.dat":
        "https://github.com/vatsimnetwork/vatspy-data-project/releases/latest/download/VATSpy.dat",
    "Boundaries.geojson":
        "https://github.com/vatsimnetwork/vatspy-data-project/releases/latest/download/Boundaries.geojson",
    "TRACONBoundaries.geojson":
        "https://github.com/vatsimnetwork/simaware-tracon-project/releases/latest/download/TRACONBoundaries.geojson",
}
CACHE_DIR = os.path.join(USER_DATA_DIR, "sectors")
BUNDLED_DIR = os.path.join(RESOURCE_DIR, "data")
MAX_AGE_DAYS = 7
TIMEOUT_SECONDS = 60

SECTOR_POSITIONS = ("CTR", "FSS")
TRACON_POSITIONS = ("APP", "DEP")


class SectorDataError(Exception):
    """The sector data couldn't be loaded. The message is user-readable."""


def _read_vatspy(text):
    """VATSpy.dat -> {"airports": {ident: {icao, name, lat, lon}},
    "firs": {callsign prefix or ICAO: (name, boundary id)},
    "uirs": {ICAO: (name, [FIR ICAOs])}}. Raises ValueError."""
    airports, firs, fir_by_icao, uirs = {}, {}, {}, {}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().upper()
            continue
        parts = [p.strip() for p in line.split("|")]
        if section == "AIRPORTS" and len(parts) >= 4:
            try:
                lat, lon = float(parts[2]), float(parts[3])
            except ValueError:
                continue
            airport = {"icao": parts[0].upper(), "name": parts[1], "lat": lat, "lon": lon}
            pseudo = len(parts) > 6 and parts[6] == "1"
            if not pseudo:
                airports[airport["icao"]] = airport
            if len(parts) > 4 and parts[4]:
                airports.setdefault(parts[4].upper(), airport)
            airports.setdefault(airport["icao"], airport)
        elif section == "FIRS" and len(parts) >= 3:
            icao, name, prefix = parts[0].upper(), parts[1], parts[2].upper()
            boundary = (parts[3] if len(parts) > 3 and parts[3] else icao).upper()
            if prefix:
                firs.setdefault(prefix, (name, boundary))
            fir_by_icao.setdefault(icao, (name, boundary))
            fir_by_icao.setdefault(icao.replace("-", "_"), (name, boundary))
        elif section == "UIRS" and len(parts) >= 3:
            members = [f.strip().upper() for f in parts[2].split(",") if f.strip()]
            uirs[parts[0].upper()] = (parts[1], members)
    if not firs and not fir_by_icao:
        raise ValueError("no [FIRs] section")
    for icao, fir in fir_by_icao.items():
        firs.setdefault(icao, fir)
    return {"airports": airports, "firs": firs, "uirs": uirs}


def _read_features(text):
    """A GeoJSON FeatureCollection -> its features. Raises ValueError."""
    data = json.loads(text)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list) or not features:
        raise ValueError("no features")
    return [f for f in features if isinstance(f, dict) and isinstance(f.get("geometry"), dict)]


_READERS = {
    "VATSpy.dat": _read_vatspy,
    "Boundaries.geojson": _read_features,
    "TRACONBoundaries.geojson": _read_features,
}


def _download(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Flyt-ACARS",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
    return body.decode("utf-8-sig")


def refresh_cache(max_age_days=MAX_AGE_DAYS, force=False):
    """Downloads each file in SOURCES whose cached copy is missing or older
    than max_age_days (all of them with force=True). Never raises: returns
    {file name: "fresh" | "updated" | "<why it failed>"}."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    status = {}
    for name, url in SOURCES.items():
        path = os.path.join(CACHE_DIR, name)
        try:
            age_days = (time.time() - os.path.getmtime(path)) / 86400
        except OSError:
            age_days = None
        if not force and age_days is not None and age_days < max_age_days:
            status[name] = "fresh"
            continue
        try:
            text = _download(url)
            _READERS[name](text)
            temp = path + ".download"
            with open(temp, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(temp, path)
            status[name] = "updated"
        except urllib.error.HTTPError as e:
            status[name] = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            status[name] = f"couldn't download ({getattr(e, 'reason', e)})"
        except ValueError as e:
            status[name] = f"downloaded file couldn't be read ({e})"
    return status


def _load(name):
    """(parsed file, where it came from): the cached copy if it reads,
    else the bundled one. (None, None) if neither does."""
    for source, folder in (("cache", CACHE_DIR), ("bundled", BUNDLED_DIR)):
        try:
            with open(os.path.join(folder, name), encoding="utf-8-sig") as f:
                return _READERS[name](f.read()), source
        except (OSError, ValueError):
            continue
    return None, None


def load_sector_data():
    """A SectorData from the cached files, falling back to the bundled ones.
    Raises SectorDataError if VATSpy.dat or Boundaries.geojson can't be
    read from either. Without TRACONBoundaries.geojson, approach controllers
    are shown at their airport instead."""
    vatspy, vatspy_from = _load("VATSpy.dat")
    firs, firs_from = _load("Boundaries.geojson")
    tracons, tracons_from = _load("TRACONBoundaries.geojson")
    if vatspy is None or firs is None:
        raise SectorDataError("The ATC sector data couldn't be loaded, and no copy came with the app.")
    data = SectorData(vatspy, firs, tracons or [])
    data.sources = {"VATSpy.dat": vatspy_from, "Boundaries.geojson": firs_from,
                    "TRACONBoundaries.geojson": tracons_from}
    return data


def _polygons(geometry):
    """A Polygon or MultiPolygon geometry -> a list of polygons."""
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiPolygon":
        return list(geometry.get("coordinates") or [])
    return []


def _multipolygon(geometries):
    polygons = [p for g in geometries for p in _polygons(g) if p]
    return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None


def _centre(geometry):
    """[lon, lat] in the middle of a geometry's bounding box, by its
    largest polygon (so one spanning the antimeridian isn't put halfway
    round the world)."""
    rings = [poly[0] for poly in geometry["coordinates"] if poly and poly[0]]
    ring = max(rings, key=len)
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return [(min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2]


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prefixes(callsign):
    """"EDGG_N_CTR" -> ["EDGG_N", "EDGG"]: the callsign without its
    position, then shorter and shorter."""
    parts = [p for p in str(callsign).upper().split("_") if p]
    if len(parts) > 1:
        parts = parts[:-1]
    return ["_".join(parts[:n]) for n in range(len(parts), 0, -1)]


class SectorData:
    def __init__(self, vatspy, fir_features, tracon_features):
        self.airports = vatspy["airports"]
        self.firs = vatspy["firs"]
        self.uirs = vatspy["uirs"]
        self.sources = {}

        self.boundaries = {}
        for feature in fir_features:
            boundary_id = str((feature.get("properties") or {}).get("id") or "").upper()
            if boundary_id:
                self.boundaries.setdefault(boundary_id, []).append(feature)

        self.tracons = {}
        for feature in tracon_features:
            props = feature.get("properties") or {}
            prefixes = props.get("prefix") or []
            if isinstance(prefixes, str):
                prefixes = [prefixes]
            for prefix in prefixes:
                self.tracons.setdefault(str(prefix).upper(), []).append(feature)

    def place(self, controllers):
        """Groups controllers (core.networks controller entries) by where
        they go on the map. See the module docstring for the result."""
        sectors, stations, unplaced = {}, {}, []
        for controller in controllers or []:
            position = controller.get("position")
            sector = None
            if position in SECTOR_POSITIONS:
                sector = self._fir_sector(controller["callsign"])
            elif position in TRACON_POSITIONS:
                sector = self._tracon_sector(controller["callsign"], position)
            if sector is not None:
                sectors.setdefault(sector["id"], sector)["controllers"].append(controller)
                continue
            station = self._station(controller)
            if station is not None:
                stations.setdefault(station["icao"], station)["controllers"].append(controller)
            else:
                unplaced.append(controller)
        return {"sectors": list(sectors.values()), "stations": list(stations.values()),
                "unplaced": unplaced}

    def _fir_sector(self, callsign):
        for prefix in _prefixes(callsign):
            if prefix in self.firs:
                name, boundary_id = self.firs[prefix]
                features = self.boundaries.get(boundary_id)
                if features:
                    return self._sector(f"fir:{boundary_id}", "fir", name,
                                        [f["geometry"] for f in features], features[0])
            if prefix in self.uirs:
                name, members = self.uirs[prefix]
                geometries = []
                for icao in members:
                    fir = self.firs.get(icao)
                    for feature in self.boundaries.get(fir[1] if fir else icao, []):
                        geometries.append(feature["geometry"])
                if geometries:
                    return self._sector(f"uir:{prefix}", "uir", name, geometries, None)
        return None

    def _tracon_sector(self, callsign, position):
        for prefix in _prefixes(callsign):
            features = self.tracons.get(prefix)
            if not features:
                continue
            suffixed = [f for f in features if str((f.get("properties") or {}).get("suffix") or "").upper() == position]
            plain = [f for f in features if not (f.get("properties") or {}).get("suffix")]
            matching = suffixed or plain
            if not matching:
                continue
            feature = matching[0]
            props = feature.get("properties") or {}
            tracon_id = str(props.get("id") or prefix)
            return self._sector(f"tracon:{tracon_id}:{prefix}:{position if suffixed else ''}", "tracon",
                                str(props.get("name") or tracon_id), [feature["geometry"]], feature)
        return None

    @staticmethod
    def _sector(sector_id, kind, name, geometries, label_feature):
        geometry = _multipolygon(geometries)
        if geometry is None:
            return None
        props = (label_feature or {}).get("properties") or {}
        lon, lat = _number(props.get("label_lon")), _number(props.get("label_lat"))
        label = [lon, lat] if lon is not None and lat is not None else _centre(geometry)
        return {"id": sector_id, "kind": kind, "name": name, "geometry": geometry,
                "label": label, "controllers": []}

    def _station(self, controller):
        """A marker for a controller without an outline: their airport, or
        failing that the position the network gave (IVAO)."""
        prefixes = _prefixes(controller["callsign"])
        for prefix in prefixes:
            airport = self.airports.get(prefix)
            if airport is not None:
                return {**airport, "controllers": []}
        lat, lon = controller.get("lat"), controller.get("lon")
        if lat is not None and lon is not None:
            return {"icao": prefixes[-1] if prefixes else controller["callsign"],
                    "name": "", "lat": lat, "lon": lon, "controllers": []}
        return None


if __name__ == "__main__":
    from core.networks import NetworkError, fetch_snapshot

    print(f"Cache: {CACHE_DIR}")
    for name, result in refresh_cache().items():
        print(f"  {name}: {result}")
    sectors = load_sector_data()
    print(f"Loaded from: {sectors.sources}")
    print(f"  {len(sectors.airports)} airport idents, {len(sectors.firs)} FIR keys, "
          f"{len(sectors.boundaries)} FIR outlines, {len(sectors.uirs)} UIRs, "
          f"{len(sectors.tracons)} TRACON prefixes")

    for network in ("VATSIM", "IVAO"):
        try:
            snapshot = fetch_snapshot(network)
        except NetworkError as e:
            print(f"{network}: {e}")
            continue
        placed = sectors.place(snapshot["controllers"])
        kinds = {}
        for sector in placed["sectors"]:
            kinds[sector["kind"]] = kinds.get(sector["kind"], 0) + 1
        print(f"{network}: {len(snapshot['controllers'])} controllers -> sectors {kinds}, "
              f"{len(placed['stations'])} airports, {len(placed['unplaced'])} unplaced")
        for sector in placed["sectors"][:5]:
            print(f"    {sector['kind']:6} {sector['name']:30} "
                  f"{', '.join(c['callsign'] for c in sector['controllers'])}")
        if placed["unplaced"]:
            print(f"    unplaced: {', '.join(c['callsign'] for c in placed['unplaced'][:15])}")