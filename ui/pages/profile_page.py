"""
ui/pages/profile_page.py

The Pilot Profile: career stats worked out from the logbook
(core/profile_stats.py) - totals, aircraft, airports, landings, flight
durations, records, networks and monthly activity. Only accepted flights
count; rejected ones show as the rejected count and rate. Reloads every
time the page is shown.

Usage:
    page = ProfilePage(db)
"""

import os
import sys

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame,
)
from PySide6.QtCore import Qt

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, GRADE_COLORS, font, font_heading, font_label
from ui.widgets.charts import BarChart, LineChart
from core.profile_stats import build_profile


def _card(title_text):
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


def _label(text="", size=10, color="text_primary", bold_label=False, wrap=False):
    label = QLabel(text)
    label.setFont(font_label(size) if bold_label else font(size))
    label.setStyleSheet(f"color: {PALETTE[color]};")
    label.setWordWrap(wrap)
    return label


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _hours(hours):
    total = round((hours or 0) * 60)
    return f"{total // 60:,}h {total % 60:02d}m"


def _fpm(value):
    return f"{value:,.0f} fpm" if value is not None else "—"


class ProfilePage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("ProfileScroll")
        scroll.viewport().setObjectName("ProfileViewport")
        content = QWidget()
        content.setObjectName("ProfileContent")
        scroll.setStyleSheet(f"""
            QScrollArea#ProfileScroll, QWidget#ProfileViewport, QWidget#ProfileContent {{
                background-color: {PALETTE['bg']};
                border: none;
            }}
        """)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(16)

        body.addWidget(self._build_totals())
        body.addLayout(self._pair(self._build_aircraft(), self._build_airports()))
        body.addLayout(self._pair(self._build_landing_rates(), self._build_grades()))
        body.addLayout(self._pair(self._build_trend(), self._build_durations()))
        body.addLayout(self._pair(self._build_records(), self._build_networks()))
        body.addWidget(self._build_activity())
        body.addStretch()

    @staticmethod
    def _pair(left, right):
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(left, stretch=1)
        row.addWidget(right, stretch=1)
        return row

    def _build_totals(self):
        card = QWidget()
        card.setObjectName("Card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        self.total_values = {}
        for index, (key, caption) in enumerate((("flights", "FLIGHTS"), ("hours", "HOURS"),
                                                ("distance", "DISTANCE"), ("pax", "PASSENGERS"),
                                                ("cargo", "CARGO"), ("rejected", "REJECTED"))):
            if index:
                line = QFrame()
                line.setFixedWidth(1)
                line.setStyleSheet(f"background-color: {PALETTE['border']};")
                layout.addWidget(line)
            block = QVBoxLayout()
            block.setSpacing(2)
            value = QLabel("—")
            value.setFont(font_heading(22))
            value.setAlignment(Qt.AlignCenter)
            block.addWidget(value)
            label = _label(caption, 8, "text_secondary", bold_label=True)
            label.setAlignment(Qt.AlignCenter)
            block.addWidget(label)
            layout.addLayout(block, stretch=1)
            self.total_values[key] = value
        return card

    def _build_aircraft(self):
        card, layout = _card("AIRCRAFT · HOURS BY TYPE")
        self.aircraft_chart = BarChart(horizontal=True)
        layout.addWidget(self.aircraft_chart)
        self.aircraft_note = _label("", 9, "text_secondary", bold_label=True, wrap=True)
        layout.addWidget(self.aircraft_note)
        layout.addStretch()
        return card

    def _build_airports(self):
        card, layout = _card("AIRPORTS · MOST VISITED")
        self.airports_chart = BarChart(horizontal=True)
        layout.addWidget(self.airports_chart)
        self.airports_note = _label("", 9, "text_secondary", bold_label=True, wrap=True)
        layout.addWidget(self.airports_note)
        layout.addStretch()
        return card

    def _build_landing_rates(self):
        card, layout = _card("LANDING RATES")
        self.landing_note = _label("", 9, "text_secondary", bold_label=True, wrap=True)
        layout.addWidget(self.landing_note)
        self.landing_chart = BarChart()
        layout.addWidget(self.landing_chart)
        layout.addWidget(_label("Landings per band, fpm", 8, "text_muted", bold_label=True))
        return card

    def _build_grades(self):
        card, layout = _card("LANDING GRADES")
        self.grade_note = _label("", 9, "text_secondary", bold_label=True)
        layout.addWidget(self.grade_note)
        self.grade_chart = BarChart()
        layout.addWidget(self.grade_chart)
        layout.addWidget(_label("Landings per grade", 8, "text_muted", bold_label=True))
        return card

    def _build_trend(self):
        card, layout = _card("LANDING TREND · LAST 20 FLIGHTS")
        self.trend_chart = LineChart()
        layout.addWidget(self.trend_chart)
        layout.addWidget(_label("Oldest on the left, most recent on the right", 8, "text_muted", bold_label=True))
        return card

    def _build_durations(self):
        card, layout = _card("FLIGHT DURATION")
        self.duration_chart = BarChart()
        layout.addWidget(self.duration_chart)
        layout.addWidget(_label("Flights per block time", 8, "text_muted", bold_label=True))
        return card

    def _build_records(self):
        card, layout = _card("RECORDS")
        self.records_grid = QGridLayout()
        self.records_grid.setHorizontalSpacing(14)
        self.records_grid.setVerticalSpacing(10)
        layout.addLayout(self.records_grid)
        layout.addStretch()
        return card

    def _build_networks(self):
        card, layout = _card("NETWORKS")
        self.network_chart = BarChart(horizontal=True)
        layout.addWidget(self.network_chart)
        layout.addStretch()
        return card

    def _build_activity(self):
        card, layout = _card("ACTIVITY · FLIGHTS PER MONTH")
        self.activity_chart = BarChart()
        layout.addWidget(self.activity_chart)
        return card

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        stats = build_profile(self.db.list_flights(limit=None), self.db.get_pilot())
        totals = stats["totals"]
        rate = totals["rejection_rate"]
        self.total_values["flights"].setText(f"{totals['flights']:,}")
        self.total_values["hours"].setText(_hours(totals["hours"]))
        self.total_values["distance"].setText(f"{totals['distance_nm']:,.0f} nm")
        self.total_values["pax"].setText(f"{totals['pax']:,.0f}")
        self.total_values["cargo"].setText(f"{totals['cargo_kg']:,.0f} kg")
        self.total_values["rejected"].setText(
            f"{totals['rejected']:,}" + (f"  ({rate:.0%})" if rate is not None and totals["rejected"] else ""))

        aircraft = stats["aircraft"]
        self.aircraft_chart.set_data(aircraft["hours_by_type"], value_format=lambda v: f"{v:,.1f} h")
        notes = []
        if aircraft["favourite_type"]:
            notes.append(f"FAVOURITE: {aircraft['favourite_type']}")
        if aircraft["top_airframe"]:
            reg, flights = aircraft["top_airframe"]
            notes.append(f"MOST FLOWN AIRFRAME: {reg} ({flights})")
        if aircraft["types_flown"]:
            notes.append(f"{aircraft['types_flown']} TYPES FLOWN")
        self.aircraft_note.setText("  ·  ".join(notes))

        airports = stats["airports"]
        self.airports_chart.set_data(airports["top"], value_format=lambda v: f"{v:,.0f}")
        continents = ", ".join(airports["continents"]) or "—"
        self.airports_note.setText(
            f"{airports['unique']} AIRPORTS  ·  {airports['countries']} COUNTRIES  ·  {continents.upper()}"
            if airports["unique"] else "")

        landings = stats["landings"]
        self.landing_note.setText(
            f"AVERAGE {_fpm(landings['average'])}  ·  SOFTEST {_fpm(landings['softest'])}"
            f"  ·  HARDEST {_fpm(landings['hardest'])}")
        self.landing_chart.set_data(landings["histogram"], value_format=lambda v: f"{v:,.0f}")

        average = landings["average_grade"]
        self.grade_note.setText(f"AVERAGE GRADE: {average}" if average else "AVERAGE GRADE: —")
        self.grade_chart.set_data(
            [{"label": g["label"], "value": g["value"], "color": GRADE_COLORS[g["letter"]],
              "tooltip": f"{g['label']}: {g['value']} landing{'' if g['value'] == 1 else 's'}"}
             for g in landings["grades"]],
            value_format=lambda v: f"{v:,.0f}")

        labels = landings["trend_labels"]
        self.trend_chart.set_series(
            [{"name": "Landing rate", "points": landings["trend"]}],
            x_format=lambda i: f"#{int(i)}",
            x_tooltip=lambda i: labels[int(i) - 1] if 1 <= int(i) <= len(labels) else "",
            y_format=lambda v: f"{v:,.0f} fpm")

        self.duration_chart.set_data(stats["durations"], value_format=lambda v: f"{v:,.0f}")
        self.network_chart.set_data(stats["networks"], value_format=lambda v: f"{v:,.0f}")
        self.activity_chart.set_data(stats["activity"], value_format=lambda v: f"{v:,.0f}")
        self._fill_records(stats["records"])

    def _fill_records(self, records):
        _clear(self.records_grid)
        if not records:
            self.records_grid.addWidget(_label("No flights yet", 10, "text_muted"), 0, 0)
            return
        for row, (name, value, route, flight_id) in enumerate(records):
            self.records_grid.addWidget(_label(name.upper(), 9, "text_secondary", bold_label=True), row, 0)
            self.records_grid.addWidget(_label(value, 11), row, 1)
            self.records_grid.addWidget(_label(route, 10, "text_secondary"), row, 2)
            view = QPushButton("View")
            view.setObjectName("TableActionButton")
            view.setCursor(Qt.PointingHandCursor)
            view.clicked.connect(lambda checked=False, fid=flight_id: self._open_debrief(fid))
            self.records_grid.addWidget(view, row, 3)
        self.records_grid.setColumnStretch(2, 1)

    def _open_debrief(self, flight_id):
        main_window = self.window()
        if hasattr(main_window, "open_debrief"):
            main_window.open_debrief(flight_id, return_to="profile")