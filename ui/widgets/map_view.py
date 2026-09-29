"""
ui/widgets/map_view.py

The shared map widget: a QWebEngineView showing assets/map/map.html
(MapLibre). Used by the Dashboard (historical routes) and the Live Map page
(the active flight). This widget only passes data to the map as JSON - it
never reads the database or the sim itself; the pages do that.

Usage:
    view = MapView()
    view.set_history([{"id": 12,
                       "dep": {"icao": "KLAX", "lat": 33.94, "lon": -118.41},
                       "arr": {"icao": "KSMO", "lat": 34.02, "lon": -118.45}}],
                     fit=True)
    view.highlight_flight(12)        # None clears the highlight
    view.set_live({...})             # see assets/map/map.html for the shape; None removes it
    view.reset_view()                # whole-world view

Calls made before the page has loaded are held and sent once it has (only
the latest of each kind - a burst of live updates doesn't pile up).

Load state (used by the startup splash to wait for the maps):
    view.load_state      # None while loading, then "ready" or "unavailable"
    view.ready.connect(fn)   # fn(True) once the map has been drawn,
                             # fn(False) if it can't load (no internet etc.)
The signal fires once; check load_state first in case it already has.

Note: QtWebEngineWidgets has to be imported before the QApplication is
created. main.py imports the pages (and so this module) at the top, so
that's already the case.

Run this file directly to try the map on its own:
    python -m ui.widgets.map_view
"""

import json
import os
import sys

from PySide6.QtCore import QUrl, QUrlQuery, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE

_MAP_HTML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "map", "map.html"
)


class _MapPage(QWebEnginePage):
    """Passes the map's JavaScript warnings/errors to the console, so a
    problem inside the map shows up in PyCharm's output."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level != QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel:
            print(f"[map] {message} (line {line})")


class MapView(QWebEngineView):
    ready = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._pending = {}
        self.load_state = None

        page = _MapPage(self)
        page.setBackgroundColor(QColor(PALETTE["bg"]))
        self.setPage(page)
        self.setContextMenuPolicy(Qt.NoContextMenu)

        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        self.loadFinished.connect(self._on_load_finished)
        self.titleChanged.connect(self._on_title_changed)

        if not os.path.exists(_MAP_HTML):
            print(f"[map] Map page not found at {_MAP_HTML}")
            self.load_state = "unavailable"
            self.setHtml(
                f"<body style='background:{PALETTE['bg']};color:{PALETTE['text_secondary']};"
                f"font-family:sans-serif;display:flex;align-items:center;justify-content:center;"
                f"height:100vh;margin:0'>Map file missing: assets/map/map.html</body>"
            )
            return

        url = QUrl.fromLocalFile(_MAP_HTML)
        query = QUrlQuery()
        for key, palette_key in (("accent", "accent"), ("bg", "bg"), ("panel", "bg_panel"),
                                 ("border", "border"), ("text", "text_primary"),
                                 ("text_secondary", "text_secondary"), ("positive", "positive")):
            query.addQueryItem(key, PALETTE[palette_key])
        url.setQuery(query)
        self.load(url)

    def set_history(self, routes, fit=True):
        """routes: list of {"id", "dep": {icao, lat, lon}, "arr": {icao, lat, lon}}."""
        self._call("history", "setHistory", routes, {"fit": bool(fit)})

    def highlight_flight(self, flight_id):
        self._call("highlight", "highlightFlight", flight_id)

    def set_live(self, live):
        self._call("live", "setLive", live)

    def reset_view(self):
        self._call("view", "resetView")

    def _call(self, kind, function, *args):
        js = f"window.ACARS && ACARS.{function}({', '.join(json.dumps(a) for a in args)});"
        if self._loaded:
            self.page().runJavaScript(js)
        else:
            self._pending.pop(kind, None)
            self._pending[kind] = js

    def _set_load_state(self, state):
        if self.load_state is not None:
            return
        self.load_state = state
        self.ready.emit(state == "ready")

    def _on_title_changed(self, title):
        if title.startswith("acars-map:"):
            self._set_load_state(title.split(":", 1)[1])

    def _on_load_finished(self, ok):
        if not ok:
            print("[map] The map page failed to load.")
            self._set_load_state("unavailable")
        self._loaded = True
        for js in self._pending.values():
            self.page().runJavaScript(js)
        self._pending.clear()


if __name__ == "__main__":
    import math
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton
    from PySide6.QtCore import QTimer
    from ui.theme import load_fonts, build_stylesheet

    def great_circle(a, b, steps):
        lat1, lon1, lat2, lon2 = map(math.radians, (a[1], a[0], b[1], b[0]))
        d = 2 * math.asin(math.sqrt(math.sin((lat2 - lat1) / 2) ** 2 +
                                    math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2))
        points = []
        for i in range(steps + 1):
            f = i / steps
            A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
            x = A * math.cos(lat1) * math.cos(lon1) + B * math.cos(lat2) * math.cos(lon2)
            y = A * math.cos(lat1) * math.sin(lon1) + B * math.cos(lat2) * math.sin(lon2)
            z = A * math.sin(lat1) + B * math.sin(lat2)
            points.append([math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))])
        return points

    def bearing(a, b):
        lat1, lat2 = math.radians(a[1]), math.radians(b[1])
        dlon = math.radians(b[0] - a[0])
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return math.degrees(math.atan2(x, y)) % 360

    KLAX = {"icao": "KLAX", "lat": 33.942497, "lon": -118.40805}
    KSMO = {"icao": "KSMO", "lat": 34.015822, "lon": -118.451306}
    KJFK = {"icao": "KJFK", "lat": 40.639801, "lon": -73.7789}
    KORD = {"icao": "KORD", "lat": 41.9786, "lon": -87.9048}
    RJTT = {"icao": "RJTT", "lat": 35.552299, "lon": 139.779999}
    EGLL = {"icao": "EGLL", "lat": 51.4706, "lon": -0.461941}
    routes = [
        {"id": 1, "dep": KLAX, "arr": KSMO},
        {"id": 2, "dep": KLAX, "arr": KJFK},
        {"id": 3, "dep": KLAX, "arr": KJFK},
        {"id": 4, "dep": KLAX, "arr": RJTT},
        {"id": 5, "dep": KJFK, "arr": EGLL},
        {"id": 6, "dep": KORD, "arr": KLAX},
    ]

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    window = QWidget()
    window.setWindowTitle("MapView test")
    window.resize(1200, 750)
    layout = QVBoxLayout(window)
    view = MapView()
    view.ready.connect(lambda ok: print(f"[map test] load state: {view.load_state}"))
    layout.addWidget(view, stretch=1)

    buttons = QHBoxLayout()
    layout.addLayout(buttons)
    for label, action in (
        ("Highlight KLAX-KJFK", lambda: view.highlight_flight(2)),
        ("Highlight KLAX-RJTT", lambda: view.highlight_flight(4)),
        ("Clear highlight", lambda: view.highlight_flight(None)),
        ("Fit all routes", lambda: view.set_history(routes, fit=True)),
        ("World view", view.reset_view),
    ):
        button = QPushButton(label)
        button.clicked.connect(action)
        buttons.addWidget(button)

    view.set_history(routes, fit=False)

    planned = [[KLAX["lon"], KLAX["lat"]], [KORD["lon"], KORD["lat"]], [KJFK["lon"], KJFK["lat"]]]
    track = great_circle(planned[0], planned[1], 150) + great_circle(planned[1], planned[2], 100)[1:]
    step = {"i": 0}

    def tick():
        i = step["i"] % len(track)
        here = track[i]
        ahead = track[min(i + 1, len(track) - 1)]
        next_fix = 1 if i < 150 else 2
        remaining = [here] + planned[next_fix:]
        remaining_nm = sum(
            3440.065 * 2 * math.asin(math.sqrt(
                math.sin(math.radians(b[1] - a[1]) / 2) ** 2 +
                math.cos(math.radians(a[1])) * math.cos(math.radians(b[1])) *
                math.sin(math.radians(b[0] - a[0]) / 2) ** 2))
            for a, b in zip(remaining, remaining[1:]))
        view.set_live({
            "lat": here[1], "lon": here[0],
            "heading": bearing(here, ahead) if ahead != here else 0,
            "flown": track[:i + 1],
            "remaining": remaining,
            "popup": {"pilot": "Test Pilot", "dep": "KLAX", "arr": "KJFK",
                      "registration": "N104TW", "title": "Airbus A320neo Asobo",
                      "altitude_ft": 36000 if 10 < i < len(track) - 10 else 4500,
                      "ias_kt": 274, "remaining_nm": remaining_nm},
        })
        step["i"] += 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)

    window.show()
    sys.exit(app.exec())