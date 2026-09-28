"""
core/airports.py

Nearest-airport lookup by lat/lon, backed by the free OurAirports dataset.

This does NOT bundle the data file - you need to download it once:
    https://ourairports.com/data/airports.csv
and save it as: data/airports.csv

Why a grid index instead of brute force: the full dataset is ~80,000 rows.
Checking every row's distance on every lookup would work but is wasteful
when called once per flight (block start/end). Instead, airports are
bucketed into ~1-degree lat/lon cells; a lookup only checks the cell the
point falls in plus a ring of neighbors, expanding outward until a match
is found. No extra dependencies (no scipy/pandas) - just the csv module.

Usage:
    lookup = AirportLookup()
    lookup.load()
    airport = lookup.find_nearest(34.0522, -118.2437)
    # -> {"icao": "KLAX", "name": "Los Angeles Intl", "lat": ..., "lon": ..., "type": "large_airport"}
    # or None if nothing found within max_radius_nm
"""

import csv
import math
import os

_DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "airports.csv"
)

_VALID_TYPES = {"large_airport", "medium_airport", "small_airport"}

_GRID_SIZE_DEG = 1.0


def _haversine_nm(lat1, lon1, lat2, lon2):
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def _grid_key(lat, lon):
    return (math.floor(lat / _GRID_SIZE_DEG), math.floor(lon / _GRID_SIZE_DEG))


class AirportLookup:
    def __init__(self, csv_path=_DEFAULT_CSV_PATH):
        self.csv_path = csv_path
        self._airports = []
        self._grid = {}
        self._loaded = False

    def load(self):
        """
        Parses the OurAirports CSV and builds the grid index. Raises
        FileNotFoundError with a clear message if the CSV hasn't been
        downloaded yet, rather than failing with a confusing traceback.
        """
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(
                f"Airport data not found at {self.csv_path}. "
                f"Download it from https://ourairports.com/data/airports.csv "
                f"and save it to that path."
            )

        self._airports = []
        self._grid = {}

        with open(self.csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("type") not in _VALID_TYPES:
                    continue

                icao = (row.get("gps_code") or row.get("ident") or "").strip()
                if not icao:
                    continue

                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (KeyError, ValueError):
                    continue

                idx = len(self._airports)
                self._airports.append({
                    "icao": icao,
                    "name": row.get("name", "").strip(),
                    "lat": lat,
                    "lon": lon,
                    "type": row.get("type"),
                })
                self._grid.setdefault(_grid_key(lat, lon), []).append(idx)

        self._loaded = True

    def find_nearest(self, lat, lon, max_radius_nm=50):
        """
        Returns the nearest airport dict to (lat, lon), or None if nothing
        is within max_radius_nm. Searches the point's grid cell first, then
        expands outward ring by ring until a candidate is found or the
        search ring's minimum possible distance exceeds max_radius_nm.
        """
        if not self._loaded:
            raise RuntimeError("AirportLookup.load() must be called before find_nearest().")

        if lat is None or lon is None:
            return None

        gx, gy = _grid_key(lat, lon)
        best = None
        best_dist = None

        ring = 0
        max_ring = int(max_radius_nm / (_GRID_SIZE_DEG * 60)) + 2

        while ring <= max_ring:
            found_any_cell = False
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if max(abs(dx), abs(dy)) != ring:
                        continue
                    cell = (gx + dx, gy + dy)
                    if cell not in self._grid:
                        continue
                    found_any_cell = True
                    for idx in self._grid[cell]:
                        a = self._airports[idx]
                        d = _haversine_nm(lat, lon, a["lat"], a["lon"])
                        if d <= max_radius_nm and (best_dist is None or d < best_dist):
                            best, best_dist = a, d

            if best is not None and ring > 0:
                break
            ring += 1

        return best