"""
ui/pages/flight_plan_page.py

The Flight Plan page: the CURRENTLY DISPATCHED SimBrief plan, in detail.
Nothing dispatched (or the dispatch ended - saved, rejected, cancelled)
-> the page shows only an empty-state message.

Top: the shared plan summary (ui/widgets/plan_summary.py) with the
DISPATCHED badge. Below it, tabs:
  - OFP              SimBrief's own OFP as generated, + Open PDF
  - Departure Wx     METAR + TAF  \
  - Arrival Wx       METAR + TAF   } snapshot from when the OFP was generated
  - Alternate Wx     METAR + TAF  /  (only if the plan has an alternate)
  - Weights & Fuel   planned weights vs limits, fuel breakdown
  - Navlog           waypoint table
  - Takeoff/Landing  only if the OFP includes SimBrief's TLR section

The Home page drives this page: it calls set_dispatch() whenever a plan is
dispatched or a dispatch ends.

Usage:
    page = FlightPlanPage()
    page.set_dispatch(controller.dispatch_info)   # or None to clear
"""

import os
import sys
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QTabWidget, QTextBrowser, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QColor

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label
from ui.widgets.plan_summary import PlanSummary, fmt_hm
from ui.widgets.unit_converters import UnitConverters

MONO_FAMILY = "Consolas"

_TAB_STYLE = f"""
    QTabWidget::pane {{
        border: 1px solid {PALETTE['border']};
        top: -1px;
        background-color: {PALETTE['bg_panel']};
    }}
    QTabBar::tab {{
        background-color: {PALETTE['bg']};
        color: {PALETTE['text_secondary']};
        border: 1px solid {PALETTE['border']};
        border-bottom: none;
        padding: 7px 16px;
        margin-right: 2px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
    }}
    QTabBar::tab:selected {{
        background-color: {PALETTE['bg_panel']};
        color: {PALETTE['accent']};
    }}
    QTabBar::tab:hover:!selected {{
        color: {PALETTE['text_primary']};
    }}
"""


def _mono(size=10):
    f = QFont(MONO_FAMILY, size)
    f.setStyleHint(QFont.Monospace)
    return f


def _num(value, unit="", decimals=0):
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}{unit}"


def _section_label(text):
    label = QLabel(text)
    label.setFont(font_label(9))
    label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
    return label


def _scrollable(widget):
    """Wraps a tab's content so long content scrolls inside the tab."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(widget)
    scroll.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget {{ background-color: {PALETTE['bg_panel']}; }}")
    return scroll


class FlightPlanPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dispatch = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        self.empty_label = QLabel("No flight dispatched — fetch and dispatch a plan on the Dashboard.")
        self.empty_label.setFont(font(12))
        self.empty_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        self.empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_label, stretch=1)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        summary_card = QWidget()
        summary_card.setObjectName("Card")
        summary_card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(summary_card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        self.summary = PlanSummary(show_badge=True)
        card_layout.addWidget(self.summary)
        content_layout.addWidget(summary_card)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(_TAB_STYLE)
        self.tabs.setDocumentMode(False)
        content_layout.addWidget(self.tabs, stretch=1)

        layout.addWidget(self.content, stretch=1)
        self.set_dispatch(None)

    def set_dispatch(self, dispatch):
        """Shows the dispatched plan (FlightSessionController.dispatch_info),
        or the empty state if None. Rebuilding is skipped when nothing changed."""
        if dispatch is self._dispatch and dispatch is not None:
            return
        self._dispatch = dispatch

        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()

        self.empty_label.setVisible(dispatch is None)
        self.content.setVisible(dispatch is not None)
        if dispatch is None:
            self.summary.set_plan(None)
            return

        plan = dispatch["plan"]
        self.summary.set_plan(plan, dispatch)

        self.tabs.addTab(self._build_ofp_tab(plan), "OFP")
        weather = plan.get("weather") or {}
        self.tabs.addTab(self._build_weather_tab(plan["origin"], weather.get("origin"), plan), "Departure Wx")
        self.tabs.addTab(self._build_weather_tab(plan["destination"], weather.get("destination"), plan), "Arrival Wx")
        if plan.get("alternate"):
            self.tabs.addTab(self._build_weather_tab(plan["alternate"], weather.get("alternate"), plan), "Alternate Wx")
        self.tabs.addTab(self._build_weights_fuel_tab(plan), "Weights && Fuel")
        self.tabs.addTab(self._build_navlog_tab(plan), "Navlog")
        tlr = (plan.get("ofp") or {}).get("tlr_text")
        if tlr:
            self.tabs.addTab(self._build_text_tab(tlr), "Takeoff/Landing")

        self.tabs.addTab(UnitConverters(), "Converters")

    def _build_ofp_tab(self, plan):
        ofp = plan.get("ofp") or {}
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        generated = plan.get("generated_at")
        stamp = datetime.fromtimestamp(generated).strftime("%Y-%m-%d %H:%M") if generated else "—"
        toolbar.addWidget(_section_label(f"SIMBRIEF OFP  ·  GENERATED {stamp}"))
        toolbar.addStretch()
        pdf_button = QPushButton("Open PDF")
        pdf_button.setCursor(Qt.PointingHandCursor)
        pdf_url = ofp.get("pdf_url")
        pdf_button.setEnabled(bool(pdf_url))
        pdf_button.clicked.connect(lambda checked=False: QDesktopServices.openUrl(QUrl(pdf_url)))
        toolbar.addWidget(pdf_button)
        layout.addLayout(toolbar)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFont(_mono(10))
        browser.setStyleSheet("QTextBrowser { background-color: #FFFFFF; color: #111111; "
                              "padding: 8px; }")
        browser.document().setDefaultStyleSheet(
            f"pre, body {{ font-family: '{MONO_FAMILY}', monospace; }}"
        )
        html = ofp.get("html")
        if html:
            browser.setHtml(html)
        else:
            browser.setPlainText("SimBrief didn't include the OFP text in this plan.")
        layout.addWidget(browser, stretch=1)
        return tab

    def _build_weather_tab(self, airport, wx, plan):
        wx = wx or {}
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel(f"{airport['icao']}  ·  {airport.get('name') or ''}")
        title.setFont(font(13))
        title.setStyleSheet(f"color: {PALETTE['text_primary']};")
        layout.addWidget(title)

        generated = plan.get("generated_at")
        stamp = datetime.fromtimestamp(generated).strftime("%H:%M") if generated else "—"
        note = QLabel(f"Snapshot from when the OFP was generated ({stamp}). Re-fetch the plan for newer weather.")
        note.setFont(font(8))
        note.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(note)

        for heading, text in (("METAR", wx.get("metar")), ("TAF", wx.get("taf"))):
            layout.addSpacing(8)
            layout.addWidget(_section_label(heading))
            body = QLabel(text or "Not available in this OFP.")
            body.setFont(_mono(11))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setStyleSheet(f"color: {PALETTE['text_primary'] if text else PALETTE['text_muted']};")
            layout.addWidget(body)

        layout.addStretch()
        return _scrollable(inner)

    def _build_weights_fuel_tab(self, plan):
        weights = plan.get("weights") or {}
        fuel = plan.get("fuel") or {}
        unit = f" {plan.get('units') or 'kg'}"

        pax, pax_wt = weights.get("pax_count"), weights.get("pax_weight")
        bags, bag_wt = weights.get("bag_count"), weights.get("bag_weight")
        weight_rows = [
            ("Empty (OEW)", weights.get("oew"), None),
            (f"Passengers ({_num(pax)})", pax * pax_wt if pax is not None and pax_wt is not None else None, None),
            (f"Bags ({_num(bags)})", bags * bag_wt if bags is not None and bag_wt is not None else None, None),
            ("Cargo", weights.get("cargo"), None),
            ("Payload", weights.get("payload"), None),
            ("Zero fuel (ZFW)", weights.get("est_zfw"), weights.get("max_zfw")),
            ("Ramp", weights.get("est_ramp"), None),
            ("Takeoff (TOW)", weights.get("est_tow"), weights.get("max_tow")),
            ("Landing (LW)", weights.get("est_ldw"), weights.get("max_ldw")),
        ]
        fuel_rows = [
            ("Taxi", fuel.get("taxi")),
            ("Trip", fuel.get("enroute_burn")),
            ("Contingency", fuel.get("contingency")),
            ("Alternate", fuel.get("alternate_burn")),
            ("Final reserve", fuel.get("reserve")),
        ]
        if fuel.get("etops"):
            fuel_rows.append(("ETOPS", fuel.get("etops")))
        fuel_rows += [
            ("Extra", fuel.get("extra")),
            ("Minimum takeoff", fuel.get("min_takeoff")),
            ("Planned takeoff", fuel.get("plan_takeoff")),
            ("Block (ramp)", fuel.get("plan_ramp")),
            ("Planned landing", fuel.get("plan_landing")),
            ("Avg. fuel flow", fuel.get("avg_fuel_flow")),
            ("Tank capacity", fuel.get("max_tanks")),
        ]

        weights_table = self._value_table(
            ["WEIGHTS", "PLANNED", "MAX"],
            [([label, _num(planned, unit), _num(limit, unit) if limit else ""],
              "over" if planned is not None and limit is not None and planned > limit else None)
             for label, planned, limit in weight_rows])
        fuel_table = self._value_table(
            ["FUEL", "PLANNED"],
            [([label, _num(value, f"{unit}/hr" if label == "Avg. fuel flow" else unit)],
              "key" if label == "Block (ramp)" else None)
             for label, value in fuel_rows])

        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(weights_table)
        column.addSpacing(32)
        column.addWidget(fuel_table)
        column.addStretch()
        return _scrollable(inner)

    def _value_table(self, columns, rows):
        table = QTableWidget(len(rows), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(columns)):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
            table.horizontalHeaderItem(c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        colors = {"over": PALETTE["negative"], "key": PALETTE["accent"]}
        for r, (cells, highlight) in enumerate(rows):
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setForeground(QColor(PALETTE["text_secondary"]))
                else:
                    item.setFont(_mono(10))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if highlight in colors:
                        item.setForeground(QColor(colors[highlight]))
                table.setItem(r, c, item)

        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.resizeRowsToContents()
        height = table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth()
        height += sum(table.rowHeight(r) for r in range(table.rowCount()))
        table.setFixedHeight(height)
        return table

    def _build_navlog_tab(self, plan):
        navlog = plan.get("navlog") or []
        unit = plan.get("units") or "kg"
        columns = ["FIX", "NAME", "VIA", "ALT", "WIND", "DIST",
                   "LEG", "TOTAL", f"FUEL USED ({unit})", f"FUEL REM ({unit})"]

        table = QTableWidget(len(navlog), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        def minutes(seconds):
            return fmt_hm(seconds / 3600) if seconds is not None else "—"

        for r, wp in enumerate(navlog):
            wind = (f"{wp['wind_dir']:03d}/{wp['wind_spd']}"
                    if wp.get("wind_dir") is not None and wp.get("wind_spd") is not None else "—")
            alt = wp.get("altitude_ft")
            cells = [
                wp.get("ident") or "—",
                wp.get("name") or "",
                wp.get("airway") or "",
                f"{alt:,}" if alt is not None else "—",
                wind,
                _num(wp.get("distance_nm")),
                minutes(wp.get("time_leg_s")),
                minutes(wp.get("time_total_s")),
                _num(wp.get("fuel_used")),
                _num(wp.get("fuel_onboard")),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    item.setForeground(QColor(PALETTE["accent"]))
                table.setItem(r, c, item)

        if not navlog:
            table.setRowCount(1)
            table.setSpan(0, 0, 1, len(columns))
            table.setItem(0, 0, QTableWidgetItem("SimBrief didn't include a navlog in this plan."))
        return table

    def _build_text_tab(self, text):
        """Takeoff/Landing: SimBrief's TLR section, as it comes (HTML or plain)."""
        browser = QTextBrowser()
        browser.setFont(_mono(10))
        browser.setStyleSheet("QTextBrowser { background-color: #FFFFFF; color: #111111; "
                              "padding: 8px; }")
        if "<" in text and ">" in text:
            browser.document().setDefaultStyleSheet(
                f"pre, body {{ font-family: '{MONO_FAMILY}', monospace; }}"
            )
            browser.setHtml(text)
        else:
            browser.setPlainText(text)
        return browser


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet
    from core.db import FlightDatabase
    from core.simbrief import fetch_latest_ofp

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    pilot = FlightDatabase().get_pilot() or {}
    plan = fetch_latest_ofp(pilot.get("simbrief_id"))

    page = FlightPlanPage()
    page.set_dispatch({"plan": plan, "registration": "N104TW", "designation": plan.get("aircraft_type") or "—"})
    page.resize(1200, 800)
    page.show()
    sys.exit(app.exec())