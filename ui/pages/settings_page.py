"""
ui/pages/settings_page.py

Settings page - the pilot profile: name, SimBrief Pilot ID, VATSIM ID,
IVAO ID and home airport, in three cards (Pilot; Flight Planning; Online
Networks) centred in a column up to FORM_MAX_WIDTH wide. Saved to the pilot row via
FlightDatabase.update_pilot_profile(); pilot stats (hours, rank, flights)
are never touched from here.

- Home airport is checked against data/airports.csv (the same OurAirports
  dataset core/airports.py uses) and its name is shown under the field.
  If the CSV is missing, the airport can still be saved - it just can't
  be verified.
- After a successful save the header (pilot name, location) refreshes
  immediately via MainWindow.update_pilot_data().

Usage:
    page = SettingsPage(db)
"""

import csv
import os
import sys

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton
)
from PySide6.QtCore import Qt, QTimer

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label
from ui.widgets.dialogs import inform
from core.airports import _DEFAULT_CSV_PATH, _VALID_TYPES

FORM_MAX_WIDTH = 880
SAVED_MESSAGE_MS = 3000

_PAGE_STYLE = f"""
    QPushButton#SaveButton:hover {{ border-color: {PALETTE['accent']}; }}
"""


def _find_airport_name(icao, csv_path=_DEFAULT_CSV_PATH):
    """Returns the airport's name if `icao` is a known airport in
    airports.csv, "" if it isn't, or None if the CSV isn't available.
    Uses the same matching rule as core/airports.py (gps_code, falling
    back to ident, landable airport types only) so an airport accepted
    here is one the flight tracker can also resolve."""
    if not os.path.exists(csv_path):
        return None
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("type") not in _VALID_TYPES:
                continue
            code = (row.get("gps_code") or row.get("ident") or "").strip().upper()
            if code == icao:
                return row.get("name", "").strip() or icao
    return ""


class SettingsPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 24)

        centred = QHBoxLayout()
        centred.addStretch(1)
        column_widget = QWidget()
        column_widget.setMaximumWidth(FORM_MAX_WIDTH)
        column_widget.setStyleSheet(_PAGE_STYLE)
        centred.addWidget(column_widget, stretch=100)
        centred.addStretch(1)
        outer.addLayout(centred)
        outer.addStretch()

        column = QVBoxLayout(column_widget)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)

        card, grid = self._section("PILOT", "How you appear in Flyt, and your home airport.")
        self.name_input, name_box, _ = self._field("PILOT NAME", "e.g. Jane Doe")
        self.home_input, home_box, self.home_hint = self._field("HOME AIRPORT", "ICAO, e.g. KLAX", hint="")
        self.home_input.setMaxLength(4)
        self.home_input.textEdited.connect(self._force_uppercase)
        grid.addWidget(name_box, 0, 0)
        grid.addWidget(home_box, 0, 1)
        column.addWidget(card)

        row = QHBoxLayout()
        row.setSpacing(16)
        card, grid = self._section("FLIGHT PLANNING", "Flyt fetches your latest SimBrief flight plan with this ID.")
        self.simbrief_input, box, _ = self._field("SIMBRIEF PILOT ID", "e.g. 123456",
                                                  hint="SimBrief → Account Settings → Pilot ID")
        grid.addWidget(box, 0, 0, 1, 2)
        row.addWidget(card, stretch=1)

        card, grid = self._section("ONLINE NETWORKS", "Optional - used to check if you're connected when you "
                                                      "dispatch a flight on VATSIM or IVAO.")
        self.vatsim_input, box, _ = self._field("VATSIM ID", "e.g. 1234567")
        grid.addWidget(box, 0, 0)
        self.ivao_input, box, _ = self._field("IVAO ID", "e.g. 123456")
        grid.addWidget(box, 0, 1)
        row.addWidget(card, stretch=1)
        column.addLayout(row)

        save_row = QHBoxLayout()
        save_row.addStretch()
        self.saved_label = QLabel("")
        self.saved_label.setFont(font_label(9))
        self.saved_label.setStyleSheet(f"color: {PALETTE['positive']};")
        save_row.addWidget(self.saved_label)
        save_row.addSpacing(12)
        save_btn = QPushButton("Save Profile")
        save_btn.setObjectName("SaveButton")
        save_btn.setFont(font_label(10))
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        column.addLayout(save_row)

        self._load()

    def _section(self, title_text, description):
        """A titled card with a one-line description; returns (card, grid)
        for its fields - two equal columns."""
        card = QWidget()
        card.setObjectName("Card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(4)

        title = QLabel(title_text)
        title.setObjectName("SectionLabel")
        title.setFont(font_label(10))
        layout.addWidget(title)

        text = QLabel(description)
        text.setFont(font(9))
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(text)
        layout.addSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch()
        return card, grid

    def _field(self, label_text, placeholder, hint=None):
        """Label, input and (optional) hint line stacked in one box.
        Returns (input, box, hint label or None)."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setFont(font_label(9))
        label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(label)

        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.returnPressed.connect(self._save)
        layout.addWidget(field)

        hint_label = None
        if hint is not None:
            hint_label = QLabel(hint)
            hint_label.setFont(font(8))
            hint_label.setStyleSheet(f"color: {PALETTE['text_muted']};")
            layout.addWidget(hint_label)
        return field, box, hint_label

    def _force_uppercase(self, text):
        cursor = self.home_input.cursorPosition()
        self.home_input.setText(text.upper())
        self.home_input.setCursorPosition(cursor)

    def _load(self):
        pilot = self.db.get_pilot() or {}
        self.name_input.setText(pilot.get("name") or "")
        self.simbrief_input.setText(pilot.get("simbrief_id") or "")
        self.vatsim_input.setText(pilot.get("vatsim_id") or "")
        self.ivao_input.setText(pilot.get("ivao_id") or "")
        self.home_input.setText(pilot.get("home_airport") or "")
        self._show_home_airport_name(pilot.get("home_airport"))

    def _show_home_airport_name(self, icao):
        if not icao:
            self.home_hint.setText("")
            return
        name = _find_airport_name(icao)
        if name is None:
            self.home_hint.setText("Airport data (data/airports.csv) not found - couldn't verify.")
        else:
            self.home_hint.setText(name)

    def _save(self):
        self.saved_label.setText("")
        home = self.home_input.text().strip().upper()

        if len(home) == 4:
            name = _find_airport_name(home)
            if name == "":
                inform(self, "Profile Not Saved", f"{home} is not a known airport.")
                return

        try:
            self.db.update_pilot_profile(
                self.name_input.text(),
                simbrief_id=self.simbrief_input.text(),
                vatsim_id=self.vatsim_input.text(),
                ivao_id=self.ivao_input.text(),
                home_airport=home,
            )
        except ValueError as e:
            inform(self, "Profile Not Saved", str(e))
            return

        self._load()

        main_window = self.window()
        if hasattr(main_window, "update_pilot_data"):
            main_window.update_pilot_data(self.db.get_pilot())

        self.saved_label.setText("Saved")
        QTimer.singleShot(SAVED_MESSAGE_MS, lambda: self.saved_label.setText(""))


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet
    from core.db import FlightDatabase

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    db = FlightDatabase()
    db.init_db()

    page = SettingsPage(db)
    page.resize(900, 700)
    page.show()
    sys.exit(app.exec())