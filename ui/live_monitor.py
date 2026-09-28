"""
ui/live_monitor.py

A small PySide6 window that polls FlightSessionController on a timer and
displays live telemetry plus a running event log.

This file used to own its own connector/tracker/db/airport-lookup wiring
directly. That's now all consolidated in core/flight_session.py so it
isn't duplicated between this window and any other UI (e.g. a future
Home page live-flight card) - this file's only job now is rendering
whatever FlightSessionController.tick() returns.

Run directly:
    python ui/live_monitor.py
"""

import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QGridLayout,
    QLabel, QListWidget, QFrame
)
from PySide6.QtCore import QTimer, Qt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.flight_session import FlightSessionController

POLL_INTERVAL_MS = 1000


def fmt(value, unit="", decimals=1):
    """Format a possibly-None numeric value for display."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}{unit}"
    return f"{value}{unit}"


class LiveMonitorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ACARS - Live Monitor (MSFS)")
        self.resize(560, 640)

        self.controller = FlightSessionController()

        self._build_ui()

        if self.controller.airport_lookup_ready:
            count = len(self.controller.airport_lookup._airports)
            self.airport_status_label.setText(f"Airport DB: {count} airports loaded")
        else:
            self.airport_status_label.setText(
                "Airport DB: not found (data/airports.csv missing) - using raw coordinates"
            )
            self.airport_status_label.setStyleSheet("font-size: 11px; color: #e67e22;")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(POLL_INTERVAL_MS)

    # ---------------- UI construction ----------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.status_label = QLabel("Connecting to MSFS...")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        root.addWidget(self.status_label)

        self.airport_status_label = QLabel("")
        self.airport_status_label.setStyleSheet("font-size: 11px; color: #888;")
        root.addWidget(self.airport_status_label)

        self.state_label = QLabel("State: —")
        self.state_label.setStyleSheet("font-size: 13px;")
        root.addWidget(self.state_label)

        root.addWidget(self._divider())

        grid = QGridLayout()
        self.value_labels = {}
        fields = [
            ("aircraft", "Aircraft"),
            ("altitude", "Altitude"),
            ("airspeed_indicated", "IAS"),
            ("ground_velocity", "Ground Speed"),
            ("vertical_speed", "Vertical Speed"),
            ("heading_true", "Heading"),
            ("g_force", "G-Force (current)"),
            ("g_force_peak", "G-Force (peak)"),
            ("fuel_total_weight", "Fuel Weight"),
            ("total_weight", "Total Weight"),
            ("gear_handle_position", "Gear"),
            ("flaps_handle_index", "Flaps Index"),
            ("parking_brake", "Parking Brake"),
            ("lat_lon", "Position"),
            ("distance_nm", "Distance Flown"),
            ("fuel_burned", "Fuel Burned"),
            ("elapsed_hours", "Elapsed Block Time"),
        ]
        for row, (key, label) in enumerate(fields):
            name_lbl = QLabel(label + ":")
            name_lbl.setStyleSheet("color: #888;")
            val_lbl = QLabel("—")
            val_lbl.setStyleSheet("font-weight: 600;")
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(val_lbl, row, 1)
            self.value_labels[key] = val_lbl
        root.addLayout(grid)

        root.addWidget(self._divider())

        event_header = QLabel("Event Log")
        event_header.setStyleSheet("font-weight: bold;")
        root.addWidget(event_header)

        self.event_list = QListWidget()
        root.addWidget(self.event_list)

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    # ---------------- Polling loop ----------------

    def _tick(self):
        result = self.controller.tick()

        if not result["connected"]:
            self.status_label.setText("Not connected - retrying...")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #e74c3c;")
            self.state_label.setText("State: —")
            return

        if not result["has_data"]:
            self.status_label.setText("Connected to SimConnect - waiting for active flight...")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #f39c12;")
            self.state_label.setText("State: —")
            return

        self.status_label.setText("Connected - receiving live flight data")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #2ecc71;")
        self.state_label.setText(f"State: {result['state']}")

        self._update_values(result["data"], result["live"])

        for event in result["events"]:
            self._log_event(event)

        if result["recorded_flight_id"] is not None:
            self._log_event({"type": "saved_to_logbook", "flight_id": result["recorded_flight_id"]})

    def _update_values(self, data, live):
        aircraft = data.get("title") or data.get("atc_type") or "—"
        self.value_labels["aircraft"].setText(str(aircraft))
        self.value_labels["altitude"].setText(fmt(data.get("altitude"), " ft", 0))
        self.value_labels["airspeed_indicated"].setText(fmt(data.get("airspeed_indicated"), " kt", 0))
        self.value_labels["ground_velocity"].setText(fmt(data.get("ground_velocity"), " kt", 0))
        self.value_labels["vertical_speed"].setText(fmt(data.get("vertical_speed"), " fpm", 0))
        self.value_labels["heading_true"].setText(fmt(data.get("heading_true"), "°", 0))
        self.value_labels["g_force"].setText(fmt(data.get("g_force"), " G", 2))
        self.value_labels["g_force_peak"].setText(fmt(live.get("g_force_peak"), " G", 2))
        self.value_labels["fuel_total_weight"].setText(fmt(data.get("fuel_total_weight"), " lbs", 0))
        self.value_labels["total_weight"].setText(fmt(data.get("total_weight"), " lbs", 0))
        self.value_labels["gear_handle_position"].setText(fmt(data.get("gear_handle_position")))
        self.value_labels["flaps_handle_index"].setText(fmt(data.get("flaps_handle_index")))
        self.value_labels["parking_brake"].setText(
            "SET" if data.get("parking_brake") else "RELEASED" if data.get("parking_brake") is not None else "—"
        )
        lat, lon = data.get("latitude"), data.get("longitude")
        if lat is not None and lon is not None:
            self.value_labels["lat_lon"].setText(f"{lat:.4f}, {lon:.4f}")
        self.value_labels["distance_nm"].setText(fmt(live.get("distance_nm"), " nm", 1))
        self.value_labels["fuel_burned"].setText(fmt(live.get("fuel_burned_so_far"), " lbs", 0))
        self.value_labels["elapsed_hours"].setText(
            fmt(live.get("elapsed_hours") * 60 if live.get("elapsed_hours") is not None else None, " min", 1)
        )

    def _log_event(self, event):
        etype = event.get("type", "event")
        detail_parts = [f"{k}={v}" for k, v in event.items() if k not in ("type", "time")]
        line = f"[{etype}] " + ", ".join(detail_parts) if detail_parts else f"[{etype}]"
        self.event_list.addItem(line)
        self.event_list.scrollToBottom()

    def closeEvent(self, event):
        self.controller.disconnect()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = LiveMonitorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()