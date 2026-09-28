"""
ui/pages/home_page.py

Home page, 3 zones per the wireframe:
  Zone 1 (top, full width):  New flight from SimBrief - PLACEHOLDER,
                              SimBrief integration isn't built yet.
  Zone 2 (bottom left):      Future map with historical flights - PLACEHOLDER,
                              this is explicitly backlogged (last item per
                              earlier conversation).
  Zone 3 (bottom right):     Live telemetry - REAL, wired to
                              FlightSessionController the same way
                              ui/live_monitor.py is, via its own QTimer.

IMPORTANT: this page owns its own FlightSessionController instance and
polls the sim independently. Do not run ui/live_monitor.py at the same
time as main.py (which includes this page) - both would independently
detect the same completed flight and log it to the database twice.
"""

import math
import sys
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import QTimer, Qt

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_heading, font_label
from core.flight_session import FlightSessionController

POLL_INTERVAL_MS = 1000


def _haversine_nm(lat1, lon1, lat2, lon2):
    """Straight-line distance in nautical miles, used for 'Distance From'
    (dep point -> current position). Distance Flown is different - that's
    the cumulative path length tracked by FlightStateTracker."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def _fmt(value, unit="", decimals=1):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}{unit}"
    return f"{value}{unit}"


def _card(title_text):
    """A titled card container matching the app's #Card styling."""
    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)

    title = QLabel(title_text)
    title.setObjectName("SectionLabel")
    title.setFont(font_label(10))
    layout.addWidget(title)

    return card, layout


class HomePage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.controller = FlightSessionController(db=db)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        # ---- Zone 1: SimBrief dispatch (placeholder) ----
        outer.addWidget(self._build_zone1(), stretch=0)

        # ---- Zone 2 + Zone 3: map (wide, left) + telemetry (narrow, right) ----
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(16)
        bottom_row.addWidget(self._build_zone2(), stretch=2)
        bottom_row.addWidget(self._build_zone3(), stretch=1)
        outer.addLayout(bottom_row, stretch=1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        # Not started here anymore - polling only begins when the pilot
        # clicks "Start Flight" in Zone 3, rather than running constantly
        # whether or not a flight is actually happening.

    # ---------------- Zone 1: SimBrief (placeholder) ----------------

    def _build_zone1(self):
        card, layout = _card("NEW FLIGHT")
        card.setFixedHeight(140)

        row = QHBoxLayout()
        msg = QLabel("SimBrief integration not built yet - dispatch a flight here once it's wired in.")
        msg.setFont(font(11))
        msg.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        msg.setWordWrap(True)
        row.addWidget(msg, stretch=1)

        fetch_btn = QPushButton("Fetch from SimBrief")
        fetch_btn.setEnabled(False)
        fetch_btn.setToolTip("SimBrief integration coming soon")
        fetch_btn.setCursor(Qt.ArrowCursor)
        row.addWidget(fetch_btn)

        layout.addLayout(row)
        return card

    # ---------------- Zone 2: Map (placeholder, backlogged) ----------------

    def _build_zone2(self):
        card, layout = _card("FLIGHT MAP")

        msg = QLabel("Live map with historical flight routes (origin → destination) - planned, not built yet.")
        msg.setFont(font(11))
        msg.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(msg, stretch=1, alignment=Qt.AlignCenter)

        return card

    # ---------------- Zone 3: Live telemetry (real) ----------------

    def _build_zone3(self):
        card, layout = _card("LIVE TELEMETRY")

        self.monitor_button = QPushButton("Start Flight")
        self.monitor_button.setCursor(Qt.PointingHandCursor)
        self.monitor_button.clicked.connect(self._toggle_monitoring)
        layout.addWidget(self.monitor_button)

        self.save_button = QPushButton("Save Flight")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setVisible(False)  # only shown once a flight completes
        self.save_button.clicked.connect(self._save_pending_flight)
        layout.addWidget(self.save_button)

        self.status_label = QLabel("Not monitoring")
        self.status_label.setFont(font(10))
        self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(self.status_label)

        self.state_label = QLabel("State: —")
        self.state_label.setFont(font_label(9))
        self.state_label.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(self.state_label)

        self.sim_label = QLabel("")
        self.sim_label.setFont(font_label(8))
        self.sim_label.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(self.sim_label)

        layout.addWidget(self._divider())

        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        self.value_labels = {}
        fields = [
            ("distance_from", "Distance From"),
            ("distance_to", "Distance To"),
            ("distance_flown", "Distance Flown"),
            ("elapsed_time", "Elapsed Time"),
            ("remaining_time", "Remaining Time"),
            ("altitude", "Altitude"),
            ("heading", "Heading"),
            ("airspeed_indicated", "IAS"),
            ("ground_speed", "Ground Speed"),
            ("fuel_burned", "Fuel Burned"),
            ("fuel_remaining", "Fuel Remaining"),
        ]
        for row, (key, label) in enumerate(fields):
            name_lbl = QLabel(label)
            name_lbl.setFont(font_label(9))
            name_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            val_lbl = QLabel("—")
            val_lbl.setFont(font(10))
            val_lbl.setStyleSheet(f"color: {PALETTE['text_primary']};")
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(val_lbl, row, 1)
            self.value_labels[key] = val_lbl
        layout.addLayout(grid)

        layout.addWidget(self._build_raw_state_panel())
        layout.addStretch()

        return card

    def _build_raw_state_panel(self):
        """
        'Cockpit Snapshot' - a plain white block for transient sim values
        that aren't stored anywhere (parking brake, flap/gear position) -
        nice to glance at, not part of the logbook. Deliberately styled
        distinct from the rest of the (dark) UI so it reads as a quick
        glance panel, not another themed stat block.
        """
        panel = QWidget()
        panel.setStyleSheet("background-color: #FFFFFF; border-radius: 4px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        header = QLabel("COCKPIT SNAPSHOT")
        header.setFont(font_label(8))
        header.setStyleSheet("color: #888888;")
        layout.addWidget(header)

        grid = QGridLayout()
        grid.setVerticalSpacing(4)
        self.raw_value_labels = {}
        raw_fields = [
            ("parking_brake", "Parking Brake"),
            ("flaps_handle_index", "Flaps Position"),
            ("gear_handle_position", "Gear Position"),
        ]
        for row, (key, label) in enumerate(raw_fields):
            name_lbl = QLabel(label)
            name_lbl.setFont(font_label(8))
            name_lbl.setStyleSheet("color: #666666;")
            val_lbl = QLabel("—")
            val_lbl.setFont(font(9))
            val_lbl.setStyleSheet("color: #111111;")
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(val_lbl, row, 1)
            self.raw_value_labels[key] = val_lbl
        layout.addLayout(grid)

        return panel

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background-color: {PALETTE['border']};")
        line.setFixedHeight(1)
        return line

    # ---------------- Polling ----------------

    def _toggle_monitoring(self):
        if not self.timer.isActive():
            self.timer.start(POLL_INTERVAL_MS)
            self.monitor_button.setText("Stop Monitoring")
            self.status_label.setText("Connecting...")
            self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        else:
            self.timer.stop()
            self.controller.disconnect()
            self.monitor_button.setText("Start Flight")
            self.status_label.setText("Not monitoring")
            self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            self.state_label.setText("State: —")

    def _tick(self):
        result = self.controller.tick()
        self.sim_label.setText(f"Sim: {result.get('sim_name', 'Unknown')}")

        # Safety: don't allow stopping mid-flight - if monitoring stopped
        # before block_end fires, that flight would never get logged.
        self.monitor_button.setEnabled(result["state"] != "BLOCK")

        if not result["connected"]:
            self.status_label.setText("Not connected")
            self.status_label.setStyleSheet(f"color: {PALETTE['negative']};")
            self.state_label.setText("State: —")
            return

        if not result["has_data"]:
            self.status_label.setText("Waiting for active flight...")
            self.status_label.setStyleSheet(f"color: {PALETTE['warning']};")
            self.state_label.setText("State: —")
            return

        self.status_label.setText("Receiving live data")
        self.status_label.setStyleSheet(f"color: {PALETTE['positive']};")
        self.state_label.setText(f"State: {result['state']}")

        data = result["data"]
        live = result["live"]

        dep_lat = getattr(self.controller.tracker, "dep_lat", None)
        dep_lon = getattr(self.controller.tracker, "dep_lon", None)
        cur_lat, cur_lon = data.get("latitude"), data.get("longitude")
        distance_from = _haversine_nm(dep_lat, dep_lon, cur_lat, cur_lon)

        self.value_labels["distance_from"].setText(_fmt(distance_from, " nm", 1))
        # Distance To and Remaining Time need a known destination, which
        # doesn't exist yet (no dispatch/flight-plan system built) - shown
        # as unavailable rather than guessed.
        self.value_labels["distance_to"].setText("— (no flight plan)")
        self.value_labels["remaining_time"].setText("— (no flight plan)")
        self.value_labels["distance_flown"].setText(_fmt(live.get("distance_nm"), " nm", 1))

        elapsed = live.get("elapsed_hours")
        self.value_labels["elapsed_time"].setText(_fmt(elapsed * 60 if elapsed is not None else None, " min", 1))

        self.value_labels["altitude"].setText(_fmt(data.get("altitude"), " ft", 0))
        self.value_labels["heading"].setText(_fmt(data.get("heading_true"), "°", 0))
        self.value_labels["airspeed_indicated"].setText(_fmt(data.get("airspeed_indicated"), " kt", 0))
        self.value_labels["ground_speed"].setText(_fmt(data.get("ground_velocity"), " kt", 0))
        self.value_labels["fuel_burned"].setText(_fmt(live.get("fuel_burned_so_far"), " lbs", 0))
        self.value_labels["fuel_remaining"].setText(_fmt(data.get("fuel_total_weight"), " lbs", 0))

        # Raw sim state panel - not stored, just a live glance
        parking_brake = data.get("parking_brake")
        self.raw_value_labels["parking_brake"].setText(
            "SET" if parking_brake else "RELEASED" if parking_brake is not None else "—"
        )
        self.raw_value_labels["flaps_handle_index"].setText(_fmt(data.get("flaps_handle_index")))
        self.raw_value_labels["gear_handle_position"].setText(_fmt(data.get("gear_handle_position")))

        # A flight completed but hasn't been saved yet - show the Save
        # button rather than writing to the DB automatically.
        self.save_button.setVisible(result["pending_flight"])

    def _save_pending_flight(self):
        flight_id = self.controller.record_pending_flight()
        if flight_id is None:
            return  # nothing was actually pending - shouldn't normally happen

        self.save_button.setVisible(False)

        main_window = self.window()
        if hasattr(main_window, "update_pilot_data"):
            main_window.update_pilot_data(self.db.get_pilot())

        # Tell the Logbook page to reload - it only loads once at startup
        # otherwise, and wouldn't show this new flight until the app restarts.
        logbook_page = main_window.get_page("logbook") if hasattr(main_window, "get_page") else None
        if logbook_page is not None and hasattr(logbook_page, "refresh"):
            logbook_page.refresh()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet
    from core.db import FlightDatabase

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    db = FlightDatabase()
    db.init_db()

    page = HomePage(db)
    page.resize(1100, 700)
    page.show()
    sys.exit(app.exec())