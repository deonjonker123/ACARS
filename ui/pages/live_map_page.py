"""
ui/pages/live_map_page.py

Live Map: the active flight on a world map (ui/widgets/map_view.py). A plane
icon sits at the aircraft's position, turned to its heading. Hovering it
shows a card (pilot, DEP -> ARR, registration + sim aircraft title, altitude
/ IAS / distance remaining) and the route: the part flown so far solid, the
rest of the SimBrief route dashed.

This page never talks to the sim. The Dashboard owns the one
FlightSessionController and passes things on (like it does for the Flight
Plan page):
    page.set_dispatch(dispatch)    # FlightSessionController.dispatch_info, or None
    page.update_live(result)       # every poll result, or None when monitoring stops

The flown track is recorded here from the positions that come in - one
point every TRACK_MIN_SPACING_NM, thinned out if it gets long. It starts
again on a new dispatch, or if the aircraft jumps more than TRACK_JUMP_NM
between two polls (slewed / relocated in the sim). It's kept in memory only.

The remaining route is the aircraft's position -> the next waypoint of the
SimBrief navlog -> ... -> destination. "Next" is found from the route leg
the aircraft is closest to; it only ever moves forward along the route.
Distance remaining is measured along that route.

The map opens on the whole world; after that the view is left to the pilot.
"""

import math
import os
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font_label
from ui.widgets.map_view import MapView

TRACK_MIN_SPACING_NM = 0.5
TRACK_MAX_POINTS = 3000
TRACK_JUMP_NM = 20.0


def _haversine_nm(a, b):
    """Great-circle distance in nm between two [lon, lat] points."""
    r_nm = 3440.065
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dphi = p2 - p1
    dlambda = math.radians(b[0] - a[0])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(min(1.0, math.sqrt(h)))


def _is_coord(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def route_points(plan):
    """The planned route as [lon, lat] points: origin, every navlog fix with
    a position, then the destination (unless the navlog already ends there)."""
    origin, dest = plan["origin"], plan["destination"]
    points = [[origin["lon"], origin["lat"]]]
    for fix in plan.get("navlog") or []:
        if _is_coord(fix.get("lat")) and _is_coord(fix.get("lon")):
            point = [fix["lon"], fix["lat"]]
            if _haversine_nm(points[-1], point) > 0.01:
                points.append(point)
    dest_point = [dest["lon"], dest["lat"]]
    if _haversine_nm(points[-1], dest_point) > 0.5:
        points.append(dest_point)
    elif len(points) > 1:
        points[-1] = dest_point
    else:
        points.append(dest_point)
    return points


def _distance_to_leg_nm(point, a, b):
    """Distance in nm from `point` to the leg a -> b ([lon, lat] each), on a
    flat projection centred on `point` - plenty for picking the nearest leg."""
    lat0 = math.radians(point[1])

    def local(p):
        dlon = (p[0] - point[0] + 180) % 360 - 180
        return dlon * 60 * math.cos(lat0), (p[1] - point[1]) * 60

    ax, ay = local(a)
    bx, by = local(b)
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    return math.hypot(ax + t * dx, ay + t * dy)


def nearest_leg(route, point, start=0):
    """Index i of the leg route[i] -> route[i+1] closest to `point`,
    searching from leg `start` onwards (so progress never goes backwards)."""
    last_leg = len(route) - 2
    if last_leg < 0:
        return 0
    start = max(0, min(start, last_leg))
    return min(range(start, last_leg + 1),
               key=lambda i: _distance_to_leg_nm(point, route[i], route[i + 1]))


def remaining_route(route, point, leg):
    """(points, distance_nm): the aircraft's position, then the rest of the
    route after leg `leg`, and its length along those points."""
    points = [point] + route[leg + 1:]
    distance = sum(_haversine_nm(a, b) for a, b in zip(points, points[1:]))
    return points, distance


def thin_track(track):
    """Halves a long track (keeping the first and last points)."""
    return track[:1] + track[1:-1][1::2] + track[-1:]


class LiveMapPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._dispatch = None
        self._route = []
        self._leg = 0
        self._track = []
        self._pilot_name = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)

        card = QWidget()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("LIVE MAP")
        title.setObjectName("SectionLabel")
        title.setFont(font_label(10))
        title_row.addWidget(title)
        title_row.addStretch()
        self.status_label = QLabel("")
        self.status_label.setFont(font_label(9))
        title_row.addWidget(self.status_label)
        card_layout.addLayout(title_row)

        self.map = MapView()
        card_layout.addWidget(self.map, stretch=1)
        layout.addWidget(card)

        self._refresh_pilot_name()
        self._set_status("NO FLIGHT DISPATCHED", PALETTE["text_secondary"])

    def set_dispatch(self, dispatch):
        """A new dispatch (or None) resets the route and the recorded track.
        Called again with the same dispatch, it changes nothing."""
        if dispatch is self._dispatch:
            return
        self._dispatch = dispatch
        self._track = []
        self._leg = 0
        self._route = route_points(dispatch["plan"]) if dispatch is not None else []
        self._refresh_pilot_name()
        self.map.set_live(None)
        if dispatch is None:
            self._set_status("NO FLIGHT DISPATCHED", PALETTE["text_secondary"])
        else:
            self._set_status(f"{self._flight_text()}  ·  NOT TRACKING", PALETTE["text_secondary"])

    def update_live(self, result):
        """One poll result from FlightSessionController.tick(), or None when
        monitoring stops. The plane is only shown while there's live data
        for a dispatched flight."""
        if self._dispatch is None:
            self.map.set_live(None)
            return
        if result is None or not result.get("connected"):
            self.map.set_live(None)
            self._set_status(f"{self._flight_text()}  ·  NOT TRACKING", PALETTE["text_secondary"])
            return
        data = result.get("data") or {}
        lat, lon = data.get("latitude"), data.get("longitude")
        if not result.get("has_data") or not _is_coord(lat) or not _is_coord(lon):
            self.map.set_live(None)
            self._set_status(f"{self._flight_text()}  ·  WAITING FOR SIM DATA", PALETTE["warning"])
            return

        here = [lon, lat]
        self._record(here)
        self._leg = nearest_leg(self._route, here, self._leg)
        remaining, remaining_nm = remaining_route(self._route, here, self._leg)

        plan = self._dispatch["plan"]
        heading = data.get("heading_true")
        self.map.set_live({
            "lat": lat,
            "lon": lon,
            "heading": heading if _is_coord(heading) else 0,
            "flown": self._track + [here],
            "remaining": remaining,
            "popup": {
                "pilot": self._pilot_name,
                "dep": plan["origin"]["icao"],
                "arr": plan["destination"]["icao"],
                "registration": self._dispatch["registration"],
                "title": data.get("title") or "",
                "altitude_ft": data.get("altitude"),
                "ias_kt": data.get("airspeed_indicated"),
                "remaining_nm": remaining_nm,
            },
        })
        self._set_status(f"{self._flight_text()}  ·  LIVE", PALETTE["positive"])

    def _record(self, here):
        if self._track:
            moved = _haversine_nm(self._track[-1], here)
            if moved > TRACK_JUMP_NM:
                self._track = []
                self._leg = 0
            elif moved < TRACK_MIN_SPACING_NM:
                return
        self._track.append(here)
        if len(self._track) > TRACK_MAX_POINTS:
            self._track = thin_track(self._track)

    def _flight_text(self):
        plan = self._dispatch["plan"]
        number = plan.get("flight_number") or "—"
        return f"{number}  {plan['origin']['icao']} → {plan['destination']['icao']}"

    def _set_status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def _refresh_pilot_name(self):
        pilot = self.db.get_pilot() or {}
        self._pilot_name = pilot.get("name") or ""

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_pilot_name()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from ui.theme import load_fonts, build_stylesheet

    class _FakeDB:
        def get_pilot(self):
            return {"name": "Test Pilot"}

    KLAX = {"icao": "KLAX", "name": "LOS ANGELES INTL", "lat": 33.942497, "lon": -118.40805}
    KJFK = {"icao": "KJFK", "name": "JOHN F KENNEDY INTL", "lat": 40.639801, "lon": -73.7789}
    navlog = [{"ident": ident, "lat": lat, "lon": lon} for ident, lat, lon in (
        ("TRM", 33.6283, -116.1600), ("DVC", 37.8083, -108.9317), ("MCK", 40.2046, -100.5923),
        ("DSM", 41.4378, -93.6484), ("FWA", 40.9785, -85.1928), ("JHW", 42.1887, -79.1211),
        ("CAMRN", 40.0183, -73.8607), ("KJFK", KJFK["lat"], KJFK["lon"]),
    )]
    dispatch = {
        "plan": {"flight_number": "TW100", "origin": KLAX, "destination": KJFK, "navlog": navlog},
        "registration": "N104TW",
        "designation": "A320",
    }

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    page = LiveMapPage(_FakeDB())
    page.resize(1200, 750)
    page.set_dispatch(dispatch)

    route = route_points(dispatch["plan"])
    path = []
    for a, b in zip(route, route[1:]):
        steps = max(1, int(_haversine_nm(a, b) / 4))
        path += [[a[0] + (b[0] - a[0]) * i / steps + 0.05, a[1] + (b[1] - a[1]) * i / steps]
                 for i in range(steps)]
    step = {"i": 0}

    def tick():
        i = step["i"] % len(path)
        if i == 0:
            page.set_dispatch(None)
            page.set_dispatch(dispatch)
        here, ahead = path[i], path[min(i + 1, len(path) - 1)]
        heading = math.degrees(math.atan2((ahead[0] - here[0]) * math.cos(math.radians(here[1])),
                                          ahead[1] - here[1])) % 360
        page.update_live({
            "connected": True, "has_data": True,
            "data": {"latitude": here[1], "longitude": here[0], "heading_true": heading,
                     "altitude": 37000, "airspeed_indicated": 268,
                     "title": "Airbus A320neo Asobo"},
        })
        step["i"] += 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)

    page.show()
    sys.exit(app.exec())