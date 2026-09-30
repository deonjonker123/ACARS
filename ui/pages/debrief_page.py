"""
ui/pages/debrief_page.py

The flight debrief: one logged flight in full. Not in the sidebar - it's
opened from the Submit popup (Dashboard) and the logbook's View button,
and its Back button returns to where it was opened from.

  Header        ACCEPTED/REJECTED badge, flight, route, aircraft, date,
                network, the rejection reason, and the landing grade
  Route         planned (dashed) and flown (solid) on the map
  Landing       the grade's breakdown (points off per part) and the
                touchdown details
  Profile       altitude, and IAS/GS, over the flight (two charts)
  Plan vs actual  block time, fuel, distance, cruise altitude
  Timeline      block out, takeoff, gear, flaps, touchdowns, block in

All the numbers come from core/debrief.py; this page only lays them out.

Usage:
    page = DebriefPage(db)
    page.show_flight(flight_id, return_to="logbook")   # then navigate to it
    # MainWindow.open_debrief(flight_id, return_to) does both
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
from ui.widgets.charts import LineChart, SERIES_COLORS, CHART_COLORS
from ui.widgets.map_view import MapView
from core.debrief import build_debrief

NOT_RECORDED = "Not recorded for this flight - flights are recorded in full from v1.1 on."
MAP_HEIGHT = 340


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


class DebriefPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._return_to = "logbook"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 16, 32, 24)
        outer.setSpacing(12)

        top = QHBoxLayout()
        back = QPushButton("← Back")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self._go_back)
        top.addWidget(back)
        top.addStretch()
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("DebriefScroll")
        scroll.viewport().setObjectName("DebriefViewport")
        content = QWidget()
        content.setObjectName("DebriefContent")
        scroll.setStyleSheet(f"""
            QScrollArea#DebriefScroll, QWidget#DebriefViewport, QWidget#DebriefContent {{
                background-color: {PALETTE['bg']};
                border: none;
            }}
        """)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(16)
        body.addWidget(self._build_header())

        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._build_map(), stretch=3)
        row.addWidget(self._build_landing(), stretch=2)
        body.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._build_profile(), stretch=3)
        row.addWidget(self._build_plan_vs_actual(), stretch=2)
        body.addLayout(row)

        body.addWidget(self._build_timeline())
        body.addStretch()

    def _build_header(self):
        card = QWidget()
        card.setObjectName("Card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(6)
        badge_row = QHBoxLayout()
        self.badge = QLabel("")
        self.badge.setFont(font_label(9))
        badge_row.addWidget(self.badge)
        badge_row.addStretch()
        left.addLayout(badge_row)
        self.title = QLabel("")
        self.title.setFont(font_heading(22))
        left.addWidget(self.title)
        self.subtitle = _label("", 10, "text_secondary", bold_label=True)
        left.addWidget(self.subtitle)
        self.reason = _label("", 10, "negative", wrap=True)
        left.addWidget(self.reason)
        layout.addLayout(left, stretch=1)

        right = QVBoxLayout()
        right.setSpacing(0)
        self.grade_letter = QLabel("—")
        self.grade_letter.setFont(font_heading(44))
        self.grade_letter.setAlignment(Qt.AlignCenter)
        right.addWidget(self.grade_letter)
        self.grade_score = _label("", 10, "text_secondary", bold_label=True)
        self.grade_score.setAlignment(Qt.AlignCenter)
        right.addWidget(self.grade_score)
        caption = _label("LANDING GRADE", 8, "text_muted", bold_label=True)
        caption.setAlignment(Qt.AlignCenter)
        right.addWidget(caption)
        layout.addLayout(right)
        return card

    def _build_map(self):
        card, layout = _card("ROUTE")
        legend = QHBoxLayout()
        legend.setSpacing(16)
        for text, style in (("Flown", f"border-top: 3px solid {PALETTE['accent']};"),
                            ("Planned", f"border-top: 2px dashed {PALETTE['text_secondary']};")):
            key = QFrame()
            key.setFixedSize(18, 3)
            key.setStyleSheet(style)
            legend.addWidget(key, alignment=Qt.AlignVCenter)
            legend.addWidget(_label(text, 9, "text_secondary", bold_label=True))
        legend.addStretch()
        layout.addLayout(legend)
        self.map_view = MapView()
        self.map_view.setMinimumHeight(MAP_HEIGHT)
        layout.addWidget(self.map_view, stretch=1)
        return card

    def _build_landing(self):
        card, layout = _card("LANDING")
        self.landing_grid = QGridLayout()
        self.landing_grid.setHorizontalSpacing(12)
        self.landing_grid.setVerticalSpacing(6)
        layout.addLayout(self.landing_grid)
        self.landing_note = _label("", 9, "text_muted", wrap=True)
        layout.addWidget(self.landing_note)
        layout.addStretch()
        return card

    def _build_profile(self):
        card, layout = _card("FLIGHT PROFILE")
        layout.addWidget(_label("Altitude", 9, "text_secondary", bold_label=True))
        self.altitude_chart = LineChart()
        layout.addWidget(self.altitude_chart)
        layout.addWidget(_label("Speed", 9, "text_secondary", bold_label=True))
        self.speed_chart = LineChart()
        layout.addWidget(self.speed_chart)
        self.profile_note = _label(NOT_RECORDED, 9, "text_muted", wrap=True)
        layout.addWidget(self.profile_note)
        return card

    def _build_plan_vs_actual(self):
        card, layout = _card("PLAN VS ACTUAL")
        self.plan_grid = QGridLayout()
        self.plan_grid.setHorizontalSpacing(14)
        self.plan_grid.setVerticalSpacing(8)
        layout.addLayout(self.plan_grid)
        layout.addStretch()
        return card

    def _build_timeline(self):
        card, layout = _card("TIMELINE")
        self.timeline_grid = QGridLayout()
        self.timeline_grid.setHorizontalSpacing(18)
        self.timeline_grid.setVerticalSpacing(6)
        layout.addLayout(self.timeline_grid)
        self.timeline_note = _label(NOT_RECORDED, 9, "text_muted", wrap=True)
        layout.addWidget(self.timeline_note)
        return card

    def show_flight(self, flight_id, return_to="logbook"):
        """Loads a flight into the page. Returns False if it doesn't exist."""
        flight = self.db.get_flight(flight_id)
        if flight is None:
            return False
        self._return_to = return_to
        debrief = build_debrief(flight)
        self._fill_header(debrief)
        self.map_view.set_debrief(debrief["map"])
        self._fill_landing(debrief)
        self._fill_profile(debrief)
        self._fill_plan_vs_actual(debrief)
        self._fill_timeline(debrief)
        return True

    def _fill_header(self, debrief):
        accepted = debrief["accepted"]
        color = PALETTE["positive"] if accepted else PALETTE["negative"]
        self.badge.setText("ACCEPTED" if accepted else "REJECTED")
        self.badge.setStyleSheet(f"background-color: {color}; color: #ffffff;"
                                 f" padding: 3px 10px;")
        self.title.setText(debrief["title"])
        self.subtitle.setText(debrief["subtitle"].upper())
        self.reason.setText(debrief["reason"] or "")
        self.reason.setVisible(bool(debrief["reason"]))

        grade = debrief["grade"]
        if grade:
            self.grade_letter.setText(grade["letter"])
            self.grade_letter.setStyleSheet(f"color: {GRADE_COLORS[grade['letter']]};")
            self.grade_score.setText(f"{grade['score']:.0f} / 100")
        else:
            self.grade_letter.setText("—")
            self.grade_letter.setStyleSheet(f"color: {PALETTE['text_muted']};")
            self.grade_score.setText("No landing rate")

    def _fill_landing(self, debrief):
        _clear(self.landing_grid)
        grade = debrief["grade"]
        row = 0
        if grade:
            for part in grade["parts"]:
                recorded = part["points_off"] is not None
                self.landing_grid.addWidget(_label(part["label"], 9, "text_secondary", bold_label=True), row, 0)
                self.landing_grid.addWidget(_label(part["text"], 10, "text_primary" if recorded else "text_muted"),
                                            row, 1)
                if recorded:
                    off = part["points_off"]
                    points = _label(f"−{off}" if off else "✓", 10,
                                    "text_secondary" if off else "positive", bold_label=True)
                    points.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    points.setToolTip(f"{off} points off" if off else "Full marks")
                    self.landing_grid.addWidget(points, row, 2)
                row += 1

        if debrief["landing_details"]:
            divider = QFrame()
            divider.setFixedHeight(1)
            divider.setStyleSheet(f"background-color: {PALETTE['border']};")
            self.landing_grid.addWidget(divider, row, 0, 1, 3)
            row += 1
            for name, value in debrief["landing_details"]:
                self.landing_grid.addWidget(_label(name, 9, "text_secondary", bold_label=True), row, 0)
                self.landing_grid.addWidget(_label(value, 10), row, 1, 1, 2)
                row += 1
            self.landing_note.setText("")
        else:
            self.landing_note.setText("Graded on the landing rate only - touchdown G, bank and "
                                      "bounces are recorded from v1.1 on.")
        self.landing_grid.setColumnStretch(1, 1)

    def _fill_profile(self, debrief):
        profile = debrief["profile"]
        has_track = bool(profile["altitude"])
        self.altitude_chart.setVisible(has_track)
        self.speed_chart.setVisible(has_track)
        self.profile_note.setVisible(not has_track)
        minutes = lambda m: f"{int(m) // 60}:{int(m) % 60:02d}"
        self.altitude_chart.set_series(
            [{"name": "Altitude", "points": profile["altitude"], "color": CHART_COLORS["violet"]}],
            x_format=minutes, y_format=lambda v: f"{v:,.0f} ft", fill=True)
        self.speed_chart.set_series(
            [{"name": "IAS", "points": profile["ias"], "color": SERIES_COLORS[0]},
             {"name": "GS", "points": profile["gs"], "color": SERIES_COLORS[1]}],
            x_format=minutes, y_format=lambda v: f"{v:,.0f} kt")

    def _fill_plan_vs_actual(self, debrief):
        _clear(self.plan_grid)
        for column, heading in enumerate(("", "PLANNED", "ACTUAL", "")):
            if heading:
                label = _label(heading, 8, "text_muted", bold_label=True)
                label.setAlignment(Qt.AlignRight)
                self.plan_grid.addWidget(label, 0, column)
        for row, (name, planned, actual, diff) in enumerate(debrief["plan_vs_actual"], start=1):
            self.plan_grid.addWidget(_label(name, 9, "text_secondary", bold_label=True), row, 0)
            for column, (text, color) in enumerate(((planned, "text_secondary"), (actual, "text_primary"),
                                                    (diff, "text_secondary")), start=1):
                label = _label(text, 10, color)
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.plan_grid.addWidget(label, row, column)
        self.plan_grid.setColumnStretch(0, 1)

    def _fill_timeline(self, debrief):
        _clear(self.timeline_grid)
        rows = debrief["timeline"]
        self.timeline_note.setVisible(not rows)
        for column, heading in enumerate(("UTC", "ELAPSED", "EVENT", "DETAIL", "")):
            if heading:
                self.timeline_grid.addWidget(_label(heading, 8, "text_muted", bold_label=True), 0, column)
        for row, entry in enumerate(rows, start=1):
            self.timeline_grid.addWidget(_label(entry["time"], 10, "text_secondary"), row, 0)
            self.timeline_grid.addWidget(_label(entry["elapsed"], 10, "text_secondary"), row, 1)
            self.timeline_grid.addWidget(_label(entry["event"], 10), row, 2)
            self.timeline_grid.addWidget(_label(entry["detail"], 10, "text_secondary"), row, 3)
            if entry["warning"]:
                self.timeline_grid.addWidget(_label(f"⚠ {entry['warning']}", 10, "warning"), row, 4)
        self.timeline_grid.setColumnStretch(4, 1)

    def _go_back(self):
        main_window = self.window()
        if hasattr(main_window, "navigate_to"):
            main_window.navigate_to(self._return_to)