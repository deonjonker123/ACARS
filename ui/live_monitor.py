import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QListWidget, QFrame
)
from PySide6.QtCore import QTimer, Qt

import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.msfs import MSFSConnector
from core.flight_state import FlightStateTracker


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

        self.connector = MSFSConnector()
        self.tracker = FlightStateTracker()
        self.connected = False

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(POLL_INTERVAL_MS)

        self._try_connect()

    # ---------------- UI construction ----------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.status_label = QLabel("Connecting to MSFS...")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        root.addWidget(self.status_label)

        self.state_label = QLabel("State: —")
        self.state_label.setStyleSheet("font-size: 13px;")
        root.addWidget(self.state_label)

        root.addWidget(self._divider())

        # Grid of live stat labels
        grid = QGridLayout()
        self.value_labels = {}
        fields = [
            ("aircraft", "Aircraft"),
            ("altitude", "Altitude"),
            ("airspeed_indicated", "IAS"),
            ("ground_velocity", "Ground Speed"),
            ("vertical_speed", "Vertical Speed"),
            ("heading_true", "Heading"),
            ("heading_true_raw", "Heading (raw, pre-conversion)"),
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

    # ---------------- Connection handling ----------------

    def _try_connect(self):
        try:
            self.connector.connect()
            self.connected = True
            self.status_label.setText("Connected to MSFS")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #2ecc71;")
        except Exception as e:
            self.connected = False
            self.status_label.setText(f"Not connected - retrying... ({e})")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #e74c3c;")

    # ---------------- Polling loop ----------------

    def _tick(self):
        if not self.connected:
            self._try_connect()
            return

        try:
            data = self.connector.read()
        except Exception as e:
            self.connected = False
            self.status_label.setText(f"Connection lost - retrying... ({e})")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #e74c3c;")
            return

        # connect() succeeding only means the SimConnect handshake worked -
        # it does NOT mean you're in an active flight with real data yet.
        # read() returns None until actual flight data is available, so
        # that's the real signal for "connected and ready".
        if data is None:
            self.status_label.setText("Connected to SimConnect - waiting for active flight...")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #f39c12;")
            self.state_label.setText("State: —")
            return

        self.status_label.setText("Connected - receiving live flight data")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #2ecc71;")

        result = self.tracker.update(data)
        self.state_label.setText(f"State: {result['state']}")
        self._update_values(data, result["live"])

        for event in result["events"]:
            self._log_event(event)

    def _update_values(self, data, live):
        aircraft = data.get("title") or data.get("atc_type") or "—"
        self.value_labels["aircraft"].setText(str(aircraft))
        self.value_labels["altitude"].setText(fmt(data.get("altitude"), " ft", 0))
        self.value_labels["airspeed_indicated"].setText(fmt(data.get("airspeed_indicated"), " kt", 0))
        self.value_labels["ground_velocity"].setText(fmt(data.get("ground_velocity"), " kt", 0))
        self.value_labels["vertical_speed"].setText(fmt(data.get("vertical_speed"), " fpm", 0))
        self.value_labels["heading_true"].setText(fmt(data.get("heading_true"), "°", 0))
        self.value_labels["heading_true_raw"].setText(str(data.get("heading_true_raw")))
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
        self.connector.disconnect()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = LiveMonitorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()