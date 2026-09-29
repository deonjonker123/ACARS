"""
ui/pages/logbook_page.py

The Logbook: a sortable table of every completed flight, pulled from
FlightDatabase. A summary line above the table shows totals; a "View"
button per row opens the full recorded flight detail (the log_data JSON
blob captured by flight_state.py at block_end).

Usage:
    page = LogbookPage(db)
    page.refresh()   # call after any flight is logged, to reload the table
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ui.theme import PALETTE, GRADE_COLORS, font_label
from core.landing_grade import letter_for

COLUMNS = [
    "DATE", "FLIGHT", "DEP", "ARR", "AIRCRAFT", "REG",
    "PAX", "CARGO", "DIST (NM)", "DURATION", "LANDING RATE", "GRADE", "STATUS", ""
]


class _NumericItem(QTableWidgetItem):
    """Sorts by a real numeric value instead of comparing display strings,
    so '2.0' correctly sorts before '10.5' rather than after it."""
    def __init__(self, display_text, sort_value):
        super().__init__(display_text)
        self._sort_value = sort_value

    def __lt__(self, other):
        if isinstance(other, _NumericItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


def _format_duration(hours):
    if hours is None:
        return "—"
    total_minutes = round(hours * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m"


def _format_date(iso_string):
    if not iso_string:
        return "—"
    return iso_string.split(" ")[0].split("T")[0]

def _landing_rate_color(landing_vs):
    if landing_vs is None:
        return None
    magnitude = abs(landing_vs)
    if magnitude <= 200:
        return PALETTE["positive"]
    elif magnitude <= 600:
        return PALETTE["warning"]
    else:
        return PALETTE["negative"]


class LogbookPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)

        card = QWidget()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        outer.addWidget(card)

        title_row = QHBoxLayout()
        title = QLabel("LOGBOOK")
        title.setObjectName("SectionLabel")
        title.setFont(font_label(10))
        title_row.addWidget(title)
        title_row.addStretch()
        self.summary_label = QLabel("")
        self.summary_label.setFont(font_label(9))
        self.summary_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        title_row.addWidget(self.summary_label)
        layout.addLayout(title_row)

        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        """Reloads the table from the database. Call after any flight logs."""
        flights = self.db.list_flights(limit=10000)

        accepted = [f for f in flights if f.get("status") != "rejected"]
        rejected = len(flights) - len(accepted)
        total_nm = sum(f["distance_nm"] or 0 for f in accepted)
        total_hours = sum(f["block_hours"] or 0 for f in accepted)
        self.summary_label.setText(
            f"{len(accepted)} FLIGHTS  ·  {total_nm:,.1f} NM  ·  {_format_duration(total_hours)}"
            + (f"  ·  {rejected} REJECTED" if rejected else "")
        )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(flights))

        for row, f in enumerate(flights):
            self.table.setItem(row, 0, QTableWidgetItem(_format_date(f["logged_at"])))
            self.table.setItem(row, 1, QTableWidgetItem(f["flight_number"] or "—"))
            self.table.setItem(row, 2, QTableWidgetItem(f["departure_airport"] or "—"))
            self.table.setItem(row, 3, QTableWidgetItem(f["arrival_airport"] or "—"))
            self.table.setItem(row, 4, QTableWidgetItem(f["aircraft_designation"] or "—"))
            self.table.setItem(row, 5, QTableWidgetItem(f["aircraft_registration"] or "—"))
            self.table.setItem(row, 6, QTableWidgetItem(str(f["pax_count"]) if f["pax_count"] is not None else "—"))
            self.table.setItem(row, 7, QTableWidgetItem(f"{f['cargo_kg']:.0f}" if f["cargo_kg"] else "—"))

            dist = f["distance_nm"] or 0
            self.table.setItem(row, 8, _NumericItem(f"{dist:,.1f}", dist))

            dur = f["block_hours"] or 0
            self.table.setItem(row, 9, _NumericItem(_format_duration(dur), dur))

            landing_vs = f["landing_vs"]
            if landing_vs is not None:
                landing_text = f"{landing_vs:.0f} fpm"
                landing_sort = landing_vs
            else:
                landing_text = "—"
                landing_sort = 999999
            landing_item = _NumericItem(landing_text, landing_sort)
            color = _landing_rate_color(landing_vs)
            if color:
                landing_item.setForeground(QColor(color))
            self.table.setItem(row, 10, landing_item)

            score = f.get("landing_grade")
            letter = letter_for(score)
            grade_item = _NumericItem(letter or "—", score if score is not None else -1)
            if letter:
                grade_item.setForeground(QColor(GRADE_COLORS[letter]))
            self.table.setItem(row, 11, grade_item)

            is_rejected = f.get("status") == "rejected"
            status_item = QTableWidgetItem("REJECTED" if is_rejected else "ACCEPTED")
            status_item.setForeground(QColor(PALETTE["negative"] if is_rejected else PALETTE["positive"]))
            if is_rejected and f.get("reject_reason"):
                status_item.setToolTip(f["reject_reason"])
            self.table.setItem(row, 12, status_item)

            view_btn = QPushButton("View")
            view_btn.setObjectName("TableActionButton")
            view_btn.setCursor(Qt.PointingHandCursor)
            view_btn.clicked.connect(
                lambda checked=False, fid=f["id"]: self._show_detail(fid)
            )
            self.table.setCellWidget(row, 13, view_btn)

        self.table.setSortingEnabled(True)

    def _show_detail(self, flight_id):
        """Opens the flight's debrief (ui/pages/debrief_page.py)."""
        main_window = self.window()
        if hasattr(main_window, "open_debrief"):
            main_window.open_debrief(flight_id, return_to="logbook")