"""
ui/pages/home_page.py

Home page, 3 zones - a wide left column and a narrow right one
(LEFT_STRETCH : RIGHT_STRETCH, 65/35):
  Zone 2 (left, full height): Flight history - the last HISTORY_LIMIT logged
                              flights as routes on a map (ui/widgets/map_view.py)
                              with a paginated table below it. Clicking a row
                              highlights that route.
  Zone 1 (right, top):        Flight plan - fetch the latest SimBrief OFP,
                              pick a fleet airframe, Dispatch / Cancel. Only
                              as tall as its contents.
  Zone 3 (right, below):      Live telemetry - REAL, wired to
                              FlightSessionController via its own QTimer.

A flight only exists because of a dispatched plan (see core/flight_session.py):
Start Flight is only enabled once dispatched, block time only starts at the
planned origin, and landing anywhere but the destination/alternate rejects
the flight. When a dispatch ends (saved, rejected or cancelled) monitoring
stops and the page is ready for the next plan.

The plan summary is the shared ui/widgets/plan_summary.py block, and every
dispatch change is passed on to the Flight Plan and Live Map pages
(set_dispatch). Every poll result is passed on to the Live Map page too
(update_live; None when monitoring stops), so the app polls the sim once.

This page owns the app's only FlightSessionController.

While monitoring, the pilot's VATSIM/IVAO status (IDs from Settings) is
checked in the background every NETWORK_CHECK_INTERVAL_MS
(core/networks.py) and shown on the Network line in Live Telemetry. Each
result goes to the controller, which decides what the flight is logged as.

Route coordinates for the history map come from each flight's stored plan
(log_data -> flight_plan origin/destination/alternate). Older flights
without them are looked up by ICAO in data/airports.csv (core/airports.py),
once per code per session.
"""

import json
import math
import sys
import os
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QMessageBox, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import QTimer, Qt, QThread, Signal, QSettings

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_heading, font_label
from ui.widgets.plan_summary import PlanSummary, fmt_hm
from ui.widgets.map_view import MapView
from ui.pages.logbook_page import _format_date
from core.flight_session import FlightSessionController, FlightSaveBlocked, FlightDispatchError
from core.simbrief import fetch_latest_ofp, SimBriefError
from core.pilot import PilotProgress
from core.airports import find_airports
from core.networks import check_online

POLL_INTERVAL_MS = 1000
NETWORK_CHECK_INTERVAL_MS = 60000
SAVE_FLIGHT_INTERVAL_MS = 15000
MIN_GS_FOR_ETA_KT = 30
LEFT_STRETCH = 65
RIGHT_STRETCH = 35
HISTORY_LIMIT = 100
HISTORY_PAGE_SIZE = 10
HISTORY_ROW_HEIGHT = 28
HISTORY_HEADER_HEIGHT = 30
HISTORY_COLUMNS = ["DATE", "FLIGHT", "DEP → ARR", "REG", "DISTANCE"]

_INPUT_STYLE = f"""
    QComboBox {{
        background-color: {PALETTE['bg_input']};
        color: {PALETTE['text_primary']};
        border: 1px solid {PALETTE['border']};
        border-radius: 4px;
        padding: 6px 8px;
    }}
    QComboBox:focus {{ border-color: {PALETTE['accent_dim']}; }}
    QComboBox QAbstractItemView {{
        background-color: {PALETTE['bg_input']};
        color: {PALETTE['text_primary']};
        selection-background-color: {PALETTE['accent_bg']};
        selection-color: {PALETTE['accent']};
    }}
"""


def _haversine_nm(lat1, lon1, lat2, lon2):
    """Straight-line distance in nautical miles, used for 'Distance From'
    (dep point -> current position) and 'Distance To' (current position ->
    destination). Distance Flown is different - that's the cumulative path
    length tracked by FlightStateTracker."""
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


def _stored_airport(airport, icao):
    """An airport dict from a stored flight plan, if it's the airport
    `icao` and has coordinates - else None."""
    if not isinstance(airport, dict) or not icao:
        return None
    if (airport.get("icao") or "").upper() != icao.upper():
        return None
    lat, lon = airport.get("lat"), airport.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return {"icao": icao.upper(), "lat": lat, "lon": lon}


def _flight_endpoints(flight):
    """(dep, arr) airport dicts ({icao, lat, lon}) for a logbook row, taken
    from its stored flight plan. Either is None if the plan doesn't have
    it (older flights) - the caller then looks it up by ICAO."""
    try:
        plan = (json.loads(flight.get("log_data") or "null") or {}).get("flight_plan") or {}
    except (ValueError, AttributeError):
        plan = {}
    if not isinstance(plan, dict):
        plan = {}
    dep_icao = flight.get("departure_airport")
    arr_icao = flight.get("arrival_airport")
    dep = _stored_airport(plan.get("origin"), dep_icao)
    arr = (_stored_airport(plan.get("destination"), arr_icao)
           or _stored_airport(plan.get("alternate"), arr_icao))
    return dep, arr


def _type_key(text):
    """'TBM 850' / 'tbm-850' / 'TBM850' all compare equal - used to pre-select
    a fleet airframe matching the OFP's aircraft type."""
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


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


class _FetchWorker(QThread):
    """Fetches the SimBrief OFP off the UI thread, so the window doesn't
    freeze while waiting on the network. Emits (plan, "") or (None, error)."""
    finished_fetch = Signal(object, str)

    def __init__(self, pilot_id):
        super().__init__()
        self.pilot_id = pilot_id

    def run(self):
        try:
            self.finished_fetch.emit(fetch_latest_ofp(self.pilot_id), "")
        except SimBriefError as e:
            self.finished_fetch.emit(None, str(e))
        except Exception as e:
            self.finished_fetch.emit(None, f"Unexpected error fetching the plan: {e}")


class _NetworkWorker(QThread):
    """Runs one VATSIM/IVAO check off the UI thread (the feeds are a few MB).
    Emits the core.networks.check_online() result."""
    finished_check = Signal(object)

    def __init__(self, ids, lat, lon):
        super().__init__()
        self.ids, self.lat, self.lon = ids, lat, lon

    def run(self):
        try:
            self.finished_check.emit(check_online(self.ids, self.lat, self.lon))
        except Exception as e:
            self.finished_check.emit({"network": None, "callsign": None, "errors": {"check": str(e)}})


class HomePage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.controller = FlightSessionController(db=db)
        self.plan = None
        self._fetch_worker = None
        self._fetching = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        outer.addWidget(self._build_zone2(), stretch=LEFT_STRETCH)

        right_column = QVBoxLayout()
        right_column.setSpacing(16)
        right_column.addWidget(self._build_zone1(), stretch=0)
        right_column.addWidget(self._build_zone3(), stretch=1)
        outer.addLayout(right_column, stretch=RIGHT_STRETCH)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

        self.network_timer = QTimer(self)
        self.network_timer.setInterval(NETWORK_CHECK_INTERVAL_MS)
        self.network_timer.timeout.connect(self._start_network_check)
        self._network_worker = None
        self._network_first_check_done = False
        self._last_position = None

        self.save_timer = QTimer(self)
        self.save_timer.setInterval(SAVE_FLIGHT_INTERVAL_MS)
        self.save_timer.timeout.connect(self._save_flight_now)
        self._resume_checked = False

        self._refresh_dispatch_ui()

    def _build_zone1(self):
        card, layout = _card("FLIGHT PLAN")
        card.setStyleSheet(_INPUT_STYLE)

        fetch_row = QHBoxLayout()
        self.fetch_button = QPushButton("Fetch from SimBrief")
        self.fetch_button.setCursor(Qt.PointingHandCursor)
        self.fetch_button.clicked.connect(self._fetch_plan)
        fetch_row.addWidget(self.fetch_button)
        self.fetch_status = QLabel("No flight plan loaded.")
        self.fetch_status.setFont(font(10))
        self.fetch_status.setWordWrap(True)
        self.fetch_status.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        fetch_row.addWidget(self.fetch_status, stretch=1)
        layout.addLayout(fetch_row)

        self.plan_summary = PlanSummary(show_badge=False, columns=3)
        layout.addWidget(self.plan_summary)

        aircraft_label = QLabel("AIRCRAFT")
        aircraft_label.setFont(font_label(9))
        aircraft_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(aircraft_label)

        dispatch_row = QHBoxLayout()
        self.aircraft_combo = QComboBox()
        self.aircraft_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.aircraft_combo.setMinimumContentsLength(12)
        dispatch_row.addWidget(self.aircraft_combo, stretch=1)
        self.network_combo = QComboBox()
        self.network_combo.setToolTip("The network you'll fly this on - VATSIM/IVAO are checked during the flight")
        dispatch_row.addWidget(self.network_combo)
        self._populate_networks()

        self.dispatch_button = QPushButton("Dispatch")
        self.dispatch_button.setCursor(Qt.PointingHandCursor)
        self.dispatch_button.clicked.connect(self._dispatch)
        dispatch_row.addWidget(self.dispatch_button)

        self.cancel_button = QPushButton("Cancel Flight")
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.clicked.connect(self._cancel_flight)
        dispatch_row.addWidget(self.cancel_button)
        layout.addLayout(dispatch_row)

        self.dispatch_status = QLabel("")
        self.dispatch_status.setFont(font_label(9))
        self.dispatch_status.setWordWrap(True)
        layout.addWidget(self.dispatch_status)

        return card

    def _fetch_plan(self):
        pilot = self.db.get_pilot() or {}
        self.fetch_button.setEnabled(False)
        self._set_fetch_status("Fetching latest OFP from SimBrief...", PALETTE["text_secondary"])

        self._fetching = True
        self._fetch_worker = _FetchWorker(pilot.get("simbrief_id"))
        self._fetch_worker.finished_fetch.connect(self._on_plan_fetched)
        self._fetch_worker.start()

    def _on_plan_fetched(self, plan, error):
        self._fetching = False
        if error:
            self._set_fetch_status(error, PALETTE["negative"])
        else:
            self.plan = plan
            fetched = datetime.now().strftime("%H:%M")
            self._set_fetch_status(f"Latest OFP fetched at {fetched}.", PALETTE["text_secondary"])
            self._populate_aircraft(plan)
        self._refresh_dispatch_ui()

    def _set_fetch_status(self, text, color):
        self.fetch_status.setText(text)
        self.fetch_status.setStyleSheet(f"color: {color};")

    def _populate_aircraft(self, plan=None):
        """Fills the airframe picker with the active fleet the pilot is rated
        for, pre-selecting one whose name matches the OFP's aircraft type."""
        current = self.aircraft_combo.currentData()
        pilot = self.db.get_pilot() or {}
        progress = PilotProgress(pilot.get("total_hours_flown") or 0)
        rank_by_id = {r["id"]: r for r in progress.ranks}

        rated = []
        for a in self.db.list_aircraft():
            rank = rank_by_id.get(a.get("unlock_rank") or "student_pilot", progress.ranks[0])
            if progress.total_hours >= rank["min_hours"]:
                rated.append(a)

        self.aircraft_combo.clear()
        self.aircraft_combo.addItem("Select aircraft...", None)
        for a in rated:
            self.aircraft_combo.addItem(f"{a['designation']}  —  {a['registration']}", a["registration"])

        target = None
        if plan is not None:
            wanted = _type_key(plan.get("aircraft_type"))
            match = next((a for a in rated if _type_key(a["designation"]) == wanted), None)
            if match is not None:
                target = match["registration"]
        if target is None:
            target = current
        index = self.aircraft_combo.findData(target) if target else -1
        self.aircraft_combo.setCurrentIndex(max(index, 0))

    def _dispatch(self):
        if self.plan is None:
            return
        try:
            network = self.network_combo.currentData() or "OFFLINE"
            self.controller.dispatch(self.plan, self.aircraft_combo.currentData(), network=network)
        except FlightDispatchError as e:
            QMessageBox.warning(self, "Not Dispatched", str(e))
            return
        QSettings("Tailwind", "ACARS").setValue("last_network", network)
        self._refresh_dispatch_ui()

    def _populate_networks(self):
        """Offline / VATSIM / IVAO, with a network greyed out if its ID
        isn't in Settings. Pre-selects the last network dispatched with."""
        pilot = self.db.get_pilot() or {}
        ids = {"VATSIM": pilot.get("vatsim_id"), "IVAO": pilot.get("ivao_id")}
        wanted = self.network_combo.currentData() or QSettings("Tailwind", "ACARS").value("last_network", "OFFLINE")

        self.network_combo.clear()
        self.network_combo.addItem("Offline", "OFFLINE")
        for network in ("VATSIM", "IVAO"):
            self.network_combo.addItem(network, network)
            if not ids[network]:
                item = self.network_combo.model().item(self.network_combo.count() - 1)
                item.setEnabled(False)
                item.setToolTip(f"Add your {network} ID in Settings first")

        index = self.network_combo.findData(wanted)
        if index < 0 or not self.network_combo.model().item(index).isEnabled():
            index = 0
        self.network_combo.setCurrentIndex(index)

    def _cancel_flight(self):
        in_progress = (self.controller.tracker.state == "BLOCK"
                       or self.controller.pending_flight_event is not None)
        if in_progress:
            answer = QMessageBox.question(
                self, "Cancel Flight",
                "Cancel this flight? Tracking stops and nothing will be logged.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.controller.cancel_dispatch()
        self._end_of_dispatch()

    def _end_of_dispatch(self):
        """A dispatch finished (saved, rejected or cancelled): stop
        monitoring and reset the page for the next flight plan."""
        self._stop_monitoring()
        self.plan = None
        self._set_fetch_status("No flight plan loaded.", PALETTE["text_secondary"])
        self.save_button.setVisible(False)
        self._refresh_dispatch_ui()

    def _refresh_dispatch_ui(self):
        dispatch = self.controller.dispatch_info
        dispatched = dispatch is not None
        monitoring = self.timer.isActive()

        self.plan_summary.set_plan(self.plan, dispatch)
        self.fetch_button.setEnabled(not dispatched and not self._fetching)
        self.aircraft_combo.setEnabled(self.plan is not None and not dispatched)
        self.network_combo.setEnabled(self.plan is not None and not dispatched)
        self.dispatch_button.setEnabled(self.plan is not None and not dispatched)
        self.dispatch_button.setVisible(not dispatched)
        self.cancel_button.setVisible(dispatched)

        if dispatched:
            self.dispatch_status.setText(
                f"DISPATCHED  ·  {dispatch['designation']} {dispatch['registration']}"
                f"  ·  {dispatch.get('network') or 'OFFLINE'}"
            )
            self.dispatch_status.setStyleSheet(f"color: {PALETTE['positive']};")
        else:
            self.dispatch_status.setText("")
        self.dispatch_status.setVisible(dispatched)

        self.monitor_button.setEnabled(monitoring or dispatched)

        main_window = self.window()
        flight_plan_page = main_window.get_page("flight_plan") if hasattr(main_window, "get_page") else None
        if flight_plan_page is not None and hasattr(flight_plan_page, "set_dispatch"):
            flight_plan_page.set_dispatch(dispatch)

        live_map_page = self._live_map_page()
        if live_map_page is not None and hasattr(live_map_page, "set_dispatch"):
            live_map_page.set_dispatch(dispatch)

    def _live_map_page(self):
        main_window = self.window()
        return main_window.get_page("live_map") if hasattr(main_window, "get_page") else None

    def _send_live(self, result):
        """Passes a poll result (or None when monitoring stops) to the Live Map page."""
        live_map_page = self._live_map_page()
        if live_map_page is not None and hasattr(live_map_page, "update_live"):
            live_map_page.update_live(result)

    def showEvent(self, event):
        super().showEvent(event)
        if self.plan is not None and self.controller.dispatch_info is None:
            self._populate_aircraft()
        if self.controller.dispatch_info is None:
            self._populate_networks()
        self.refresh_history()

        if not self._resume_checked:
            self._resume_checked = True
            QTimer.singleShot(0, self._startup_checks)

    def _build_zone2(self):
        card, layout = _card("FLIGHT HISTORY")

        self.history_map = MapView()
        self.history_map.setMinimumHeight(220)
        layout.addWidget(self.history_map, stretch=1)

        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(HISTORY_ROW_HEIGHT)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setFixedHeight(HISTORY_HEADER_HEIGHT)
        self.history_table.setFixedHeight(
            HISTORY_HEADER_HEIGHT + HISTORY_ROW_HEIGHT * HISTORY_PAGE_SIZE + 4
        )
        self.history_table.itemSelectionChanged.connect(self._on_history_selection)
        layout.addWidget(self.history_table)

        pager = QHBoxLayout()
        self.history_info = QLabel("")
        self.history_info.setFont(font_label(9))
        self.history_info.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        pager.addWidget(self.history_info, stretch=1)

        self.history_all_button = QPushButton("Show All")
        self.history_all_button.setCursor(Qt.PointingHandCursor)
        self.history_all_button.clicked.connect(self._show_all_history)
        pager.addWidget(self.history_all_button)

        self.history_prev_button = QPushButton("‹ Prev")
        self.history_prev_button.setCursor(Qt.PointingHandCursor)
        self.history_prev_button.clicked.connect(lambda: self._set_history_page(self._history_page - 1))
        pager.addWidget(self.history_prev_button)

        self.history_page_label = QLabel("")
        self.history_page_label.setFont(font_label(9))
        self.history_page_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        pager.addWidget(self.history_page_label)

        self.history_next_button = QPushButton("Next ›")
        self.history_next_button.setCursor(Qt.PointingHandCursor)
        self.history_next_button.clicked.connect(lambda: self._set_history_page(self._history_page + 1))
        pager.addWidget(self.history_next_button)
        layout.addLayout(pager)

        self._history_flights = []
        self._history_page = 0
        self._history_signature = None
        self._airport_cache = {}

        return card

    def refresh_history(self):
        """Reloads the last HISTORY_LIMIT flights. The map is only redrawn
        (and re-fitted) when the flights actually changed."""
        flights = self.db.list_flights(limit=HISTORY_LIMIT)
        signature = [(f["id"], f["departure_airport"], f["arrival_airport"],
                      f["aircraft_registration"]) for f in flights]
        if signature == self._history_signature:
            return
        self._history_signature = signature
        self._history_flights = flights

        endpoints = {f["id"]: _flight_endpoints(f) for f in flights}
        missing = set()
        for f in flights:
            dep, arr = endpoints[f["id"]]
            for airport, icao in ((dep, f["departure_airport"]), (arr, f["arrival_airport"])):
                if airport is None and icao and icao.upper() not in self._airport_cache:
                    missing.add(icao.upper())
        if missing:
            found = find_airports(missing)
            for icao in missing:
                airport = found.get(icao)
                self._airport_cache[icao] = (
                    {"icao": icao, "lat": airport["lat"], "lon": airport["lon"]} if airport else None
                )

        routes = []
        for f in flights:
            dep, arr = endpoints[f["id"]]
            dep = dep or self._airport_cache.get((f["departure_airport"] or "").upper())
            arr = arr or self._airport_cache.get((f["arrival_airport"] or "").upper())
            if dep is not None and arr is not None:
                routes.append({"id": f["id"], "dep": dep, "arr": arr})

        self.history_map.set_history(routes, fit=True)
        self._set_history_page(0)

    def _set_history_page(self, page):
        pages = max(1, math.ceil(len(self._history_flights) / HISTORY_PAGE_SIZE))
        self._history_page = min(max(page, 0), pages - 1)
        start = self._history_page * HISTORY_PAGE_SIZE
        rows = self._history_flights[start:start + HISTORY_PAGE_SIZE]

        self.history_table.blockSignals(True)
        self.history_table.clearSelection()
        self.history_table.setRowCount(len(rows))
        for row, f in enumerate(rows):
            dep = f["departure_airport"] or "—"
            arr = f["arrival_airport"] or "—"
            dist = f["distance_nm"]
            values = [
                _format_date(f["logged_at"]),
                f["flight_number"] or "—",
                f"{dep} → {arr}",
                f["aircraft_registration"] or "—",
                f"{dist:,.0f} nm" if dist is not None else "—",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, f["id"])
                self.history_table.setItem(row, col, item)
        self.history_table.blockSignals(False)
        self.history_map.highlight_flight(None)

        total = len(self._history_flights)
        if total:
            self.history_info.setText(
                f"LAST {total} FLIGHT{'S' if total != 1 else ''}  ·  "
                f"SHOWING {start + 1}–{start + len(rows)}"
            )
        else:
            self.history_info.setText("NO FLIGHTS LOGGED YET")
        self.history_page_label.setText(f"{self._history_page + 1} / {pages}")
        self.history_prev_button.setEnabled(self._history_page > 0)
        self.history_next_button.setEnabled(self._history_page < pages - 1)

    def _on_history_selection(self):
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            self.history_map.highlight_flight(None)
            return
        item = self.history_table.item(rows[0].row(), 0)
        self.history_map.highlight_flight(item.data(Qt.UserRole) if item else None)

    def _show_all_history(self):
        self.history_table.clearSelection()
        self.history_map.highlight_flight(None)
        self._history_signature = None
        self.refresh_history()

    def _build_zone3(self):
        card, layout = _card("LIVE TELEMETRY")

        self.monitor_button = QPushButton("Start Flight")
        self.monitor_button.setCursor(Qt.PointingHandCursor)
        self.monitor_button.clicked.connect(self._toggle_monitoring)
        layout.addWidget(self.monitor_button)

        self.save_button = QPushButton("Save Flight")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setVisible(False)
        self.save_button.clicked.connect(self._save_pending_flight)
        layout.addWidget(self.save_button)

        self.status_label = QLabel("Not monitoring")
        self.status_label.setFont(font(10))
        self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(self.status_label)

        self.arming_label = QLabel("")
        self.arming_label.setFont(font(9))
        self.arming_label.setWordWrap(True)
        layout.addWidget(self.arming_label)

        self.state_label = QLabel("State: —")
        self.state_label.setFont(font_label(9))
        self.state_label.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(self.state_label)

        self.sim_label = QLabel("")
        self.sim_label.setFont(font_label(8))
        self.sim_label.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(self.sim_label)

        self.network_label = QLabel("")
        self.network_label.setFont(font_label(9))
        self.network_label.setWordWrap(True)
        layout.addWidget(self.network_label)
        self._set_network_label("Network: —", PALETTE["text_muted"])

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

    def _toggle_monitoring(self):
        if not self.timer.isActive():
            if self.controller.dispatch_info is None:
                return
            self.timer.start(POLL_INTERVAL_MS)
            self.network_timer.start()
            self.save_timer.start()
            self._network_first_check_done = False
            self._last_position = None
            self._set_network_label("Network: checking...", PALETTE["text_secondary"])
            self.monitor_button.setText("Stop Monitoring")
            self.status_label.setText("Connecting...")
            self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        else:
            self._stop_monitoring()
        self._refresh_dispatch_ui()

    def _stop_monitoring(self):
        self._save_flight_now()
        self.timer.stop()
        self.network_timer.stop()
        self.save_timer.stop()
        self._set_network_label("Network: —", PALETTE["text_muted"])
        self.controller.disconnect()
        self._send_live(None)
        self.monitor_button.setText("Start Flight")
        self.status_label.setText("Not monitoring")
        self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        self.arming_label.setText("")
        self.state_label.setText("State: —")

    def _save_flight_now(self):
        """Saves the flight in progress, with the Live Map's track."""
        live_map_page = self._live_map_page()
        track = live_map_page.track_state() if hasattr(live_map_page, "track_state") else []
        self.controller.save_active_flight(ui_state={"track": track})

    def _startup_checks(self):
        """Once the window is up: offer to resume an interrupted flight,
        then welcome a new pilot whose profile isn't set up yet."""
        self._offer_resume()
        self._offer_welcome()

    def _offer_welcome(self):
        """New pilot (no name or SimBrief ID yet): point them at Settings."""
        pilot = self.db.get_pilot() or {}
        missing = []
        if not (pilot.get("name") or "").strip():
            missing.append("your pilot name")
        if not (pilot.get("simbrief_id") or "").strip():
            missing.append("your SimBrief Pilot ID")
        if not missing:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Welcome to Tailwind")
        box.setIcon(QMessageBox.Information)
        box.setText("Welcome aboard!")
        box.setInformativeText(
            f"Before your first flight, set {' and '.join(missing)} in Settings.\n\n"
            "Your SimBrief Pilot ID is the number on simbrief.com under Account Settings - "
            "Tailwind fetches your flight plans with it. VATSIM and IVAO IDs are optional."
        )
        box.addButton("Open Settings", QMessageBox.AcceptRole)
        box.exec()

        main_window = self.window()
        if hasattr(main_window, "navigate_to"):
            main_window.navigate_to("settings")

    def _offer_resume(self):
        """At startup: if the app closed during a flight, offer to carry on."""
        saved = self.controller.load_active_flight()
        if saved is None:
            return

        dispatch = saved["dispatch"]
        plan = dispatch["plan"]
        tracker = saved.get("tracker") or {}
        lines = [
            f"{plan.get('flight_number') or '—'}   {plan['origin']['icao']} → {plan['destination']['icao']}"
            f"   ·   {dispatch['designation']} {dispatch['registration']}"
            f"   ·   {dispatch.get('network') or 'OFFLINE'}",
        ]
        if saved.get("pending_flight_event"):
            lines.append("Landed - waiting to be saved.")
        elif tracker.get("state") == "BLOCK" and tracker.get("block_start_time"):
            started = datetime.fromtimestamp(tracker["block_start_time"])
            minutes = int((datetime.now() - started).total_seconds() // 60)
            lines.append(f"Block started {started:%H:%M} ({minutes // 60}h {minutes % 60:02d}m ago), "
                         f"{tracker.get('distance_nm') or 0:,.0f} nm flown.")
        else:
            lines.append("Dispatched - not started yet.")
        lines.append("\nResume carries on tracking it. If your sim closed too, choose "
                     "Discard (no penalty) - resuming would see the aircraft somewhere else.")

        box = QMessageBox(self)
        box.setWindowTitle("Flight in progress")
        box.setIcon(QMessageBox.Question)
        box.setText("The app closed during a flight.")
        box.setInformativeText("\n".join(lines))
        resume = box.addButton("Resume", QMessageBox.AcceptRole)
        box.addButton("Discard", QMessageBox.DestructiveRole)
        box.setDefaultButton(resume)
        box.exec()

        if box.clickedButton() is not resume:
            self.controller.clear_active_flight()
            return
        self._resume_flight(saved)

    def _resume_flight(self, saved):
        ui_state = self.controller.restore_active_flight(saved)
        self.plan = saved["dispatch"]["plan"]
        self._set_fetch_status("Resumed the flight in progress.", PALETTE["text_secondary"])
        self._refresh_dispatch_ui()

        live_map_page = self._live_map_page()
        if hasattr(live_map_page, "restore_track"):
            live_map_page.restore_track(ui_state.get("track"))

        if self.controller.pending_flight_event is not None:
            self.save_button.setVisible(True)
        elif not self.timer.isActive():
            self._toggle_monitoring()

    def _set_network_label(self, text, color):
        self.network_label.setText(text)
        self.network_label.setStyleSheet(f"color: {color};")

    def _start_network_check(self):
        """Starts a background VATSIM/IVAO check at the last known sim
        position (skipped if one is still running or there's no position yet)."""
        if self._network_worker is not None and self._network_worker.isRunning():
            return
        declared = self.controller.declared_network()
        if declared == "OFFLINE":
            self._set_network_label("Network: Offline", PALETTE["text_secondary"])
            return
        pilot = self.db.get_pilot() or {}
        user_id = pilot.get("vatsim_id" if declared == "VATSIM" else "ivao_id")
        if not user_id:
            self._set_network_label(f"Network: no {declared} ID in Settings - will log Offline",
                                    PALETTE["warning"])
            return
        ids = {declared: user_id}
        if self._last_position is None:
            return
        self._network_worker = _NetworkWorker(ids, *self._last_position)
        self._network_worker.finished_check.connect(self._on_network_checked)
        self._network_worker.start()

    def _on_network_checked(self, result):
        if not self.timer.isActive():
            return
        self.controller.record_network_check(result)
        network = result.get("network")
        if network is not None:
            callsign = result.get("callsign")
            text = f"Network: {network}" + (f"  ·  {callsign}" if callsign else "")
            self._set_network_label(text, PALETTE["positive"])
        elif result.get("errors"):
            reasons = "; ".join(result["errors"].values())
            self._set_network_label(f"Network: couldn't check ({reasons})", PALETTE["warning"])
        else:
            declared = self.controller.declared_network()
            self._set_network_label(f"Network: not seen on {declared}", PALETTE["warning"])

    def _set_arming(self, text):
        if not text:
            self.arming_label.setText("")
            return
        if text.startswith("At "):
            color = PALETTE["positive"]
        elif text.startswith("Flight complete"):
            color = PALETTE["accent"]
        else:
            color = PALETTE["warning"]
        self.arming_label.setText(text)
        self.arming_label.setStyleSheet(f"color: {color};")

    def _tick(self):
        result = self.controller.tick()
        self.sim_label.setText(f"Sim: {result.get('sim_name', 'Unknown')}")

        #self.monitor_button.setEnabled(result["state"] != "BLOCK")

        rejection = next((e for e in result["events"] if e["type"] == "flight_rejected"), None)
        if rejection is not None:
            self._end_of_dispatch()
            main_window = self.window()
            if hasattr(main_window, "update_pilot_data"):
                main_window.update_pilot_data(self.db.get_pilot())
            QMessageBox.critical(self, "Flight Rejected", rejection["reason"])
            return

        self._send_live(result)

        if not result["connected"]:
            self.status_label.setText("Not connected")
            self.status_label.setStyleSheet(f"color: {PALETTE['negative']};")
            self.state_label.setText("State: —")
            self._set_arming("")
            return

        if not result["has_data"]:
            self.status_label.setText("Waiting for active flight...")
            self.status_label.setStyleSheet(f"color: {PALETTE['warning']};")
            self.state_label.setText("State: —")
            self._set_arming("")
            return

        self.status_label.setText("Receiving live data")
        self.status_label.setStyleSheet(f"color: {PALETTE['positive']};")
        self.state_label.setText(f"State: {result['state']}")
        self._set_arming(result.get("arming"))

        data = result["data"]
        live = result["live"]
        cur_lat, cur_lon = data.get("latitude"), data.get("longitude")

        if cur_lat is not None and cur_lon is not None:
            self._last_position = (cur_lat, cur_lon)
            if not self._network_first_check_done:
                self._network_first_check_done = True
                self._start_network_check()

        dep_lat = getattr(self.controller.tracker, "dep_lat", None)
        dep_lon = getattr(self.controller.tracker, "dep_lon", None)
        distance_from = _haversine_nm(dep_lat, dep_lon, cur_lat, cur_lon)
        self.value_labels["distance_from"].setText(_fmt(distance_from, " nm", 1))

        dispatch = result.get("dispatch")
        distance_to = None
        if dispatch is not None:
            dest = dispatch["plan"]["destination"]
            distance_to = _haversine_nm(cur_lat, cur_lon, dest["lat"], dest["lon"])
        self.value_labels["distance_to"].setText(_fmt(distance_to, " nm", 1))

        ground_speed = data.get("ground_velocity")
        if distance_to is not None and ground_speed and ground_speed >= MIN_GS_FOR_ETA_KT:
            self.value_labels["remaining_time"].setText(fmt_hm(distance_to / ground_speed))
        else:
            self.value_labels["remaining_time"].setText("—")

        self.value_labels["distance_flown"].setText(_fmt(live.get("distance_nm"), " nm", 1))

        elapsed = live.get("elapsed_hours")
        self.value_labels["elapsed_time"].setText(_fmt(elapsed * 60 if elapsed is not None else None, " min", 1))

        self.value_labels["altitude"].setText(_fmt(data.get("altitude"), " ft", 0))
        self.value_labels["heading"].setText(_fmt(data.get("heading_true"), "°", 0))
        self.value_labels["airspeed_indicated"].setText(_fmt(data.get("airspeed_indicated"), " kt", 0))
        self.value_labels["ground_speed"].setText(_fmt(ground_speed, " kt", 0))
        self.value_labels["fuel_burned"].setText(_fmt(live.get("fuel_burned_so_far"), " lbs", 0))
        self.value_labels["fuel_remaining"].setText(_fmt(data.get("fuel_total_weight"), " lbs", 0))

        parking_brake = data.get("parking_brake")
        self.raw_value_labels["parking_brake"].setText(
            "SET" if parking_brake else "RELEASED" if parking_brake is not None else "—"
        )
        self.raw_value_labels["flaps_handle_index"].setText(_fmt(data.get("flaps_handle_index")))
        self.raw_value_labels["gear_handle_position"].setText(_fmt(data.get("gear_handle_position")))

        self.save_button.setVisible(result["pending_flight"])

    def _save_pending_flight(self):
        try:
            flight_id = self.controller.record_pending_flight()
        except FlightSaveBlocked as e:
            QMessageBox.warning(self, "Flight Not Saved", str(e))
            return
        if flight_id is None:
            return

        self._end_of_dispatch()

        main_window = self.window()
        if hasattr(main_window, "update_pilot_data"):
            main_window.update_pilot_data(self.db.get_pilot())

        logbook_page = main_window.get_page("logbook") if hasattr(main_window, "get_page") else None
        if logbook_page is not None and hasattr(logbook_page, "refresh"):
            logbook_page.refresh()

        aircraft_page = main_window.get_page("aircraft") if hasattr(main_window, "get_page") else None
        if aircraft_page is not None and hasattr(aircraft_page, "refresh"):
            aircraft_page.refresh()

        self.refresh_history()


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