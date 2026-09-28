"""
ui/pages/settings_page.py

Settings page - the pilot profile: name, SimBrief Pilot ID, VATSIM ID,
IVAO ID and home airport. Saved to the pilot row via
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
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt, QTimer

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label
from core.airports import _DEFAULT_CSV_PATH, _VALID_TYPES

FORM_MAX_WIDTH = 560
SAVED_MESSAGE_MS = 3000

_INPUT_STYLE = f"""
    QLineEdit {{
        background-color: {PALETTE['bg_input']};
        color: {PALETTE['text_primary']};
        border: 1px solid {PALETTE['border']};
        border-radius: 4px;
        padding: 6px 8px;
    }}
    QLineEdit:focus {{ border-color: {PALETTE['accent_dim']}; }}
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
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        card = QWidget()
        card.setObjectName("Card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setMaximumWidth(FORM_MAX_WIDTH)
        card.setStyleSheet(_INPUT_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        title = QLabel("PILOT PROFILE")
        title.setObjectName("SectionLabel")
        title.setFont(font_label(10))
        layout.addWidget(title)
        layout.addSpacing(4)

        self.name_input = self._add_field(layout, "PILOT NAME", "e.g. Jane Doe")

        self.simbrief_input = self._add_field(layout, "SIMBRIEF PILOT ID", "e.g. 123456")
        layout.addWidget(self._hint("SimBrief → Account Settings → Pilot ID"))

        self.vatsim_input = self._add_field(layout, "VATSIM ID", "e.g. 1234567")
        self.ivao_input = self._add_field(layout, "IVAO ID", "e.g. 123456")

        self.home_input = self._add_field(layout, "HOME AIRPORT", "ICAO, e.g. KLAX")
        self.home_input.setMaxLength(4)
        self.home_input.textEdited.connect(self._force_uppercase)
        self.home_hint = self._hint("")
        layout.addWidget(self.home_hint)

        layout.addSpacing(8)
        save_row = QHBoxLayout()
        self.saved_label = QLabel("")
        self.saved_label.setFont(font_label(9))
        self.saved_label.setStyleSheet(f"color: {PALETTE['positive']};")
        save_row.addWidget(self.saved_label)
        save_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        layout.addLayout(save_row)

        outer.addWidget(card, alignment=Qt.AlignLeft | Qt.AlignTop)
        outer.addStretch()

        self._load()

    def _add_field(self, layout, label_text, placeholder):
        label = QLabel(label_text)
        label.setFont(font_label(9))
        label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(label)

        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.returnPressed.connect(self._save)
        layout.addWidget(field)
        return field

    def _hint(self, text):
        hint = QLabel(text)
        hint.setFont(font(8))
        hint.setStyleSheet(f"color: {PALETTE['text_muted']};")
        return hint

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
                QMessageBox.warning(self, "Profile Not Saved", f"{home} is not a known airport.")
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
            QMessageBox.warning(self, "Profile Not Saved", str(e))
            return

        self._load()  # show the cleaned values as stored (trimmed, uppercased)

        main_window = self.window()
        if hasattr(main_window, "update_pilot_data"):
            main_window.update_pilot_data(self.db.get_pilot())

        self.saved_label.setText("Saved")
        QTimer.singleShot(SAVED_MESSAGE_MS, lambda: self.saved_label.setText(""))


if __name__ == "__main__":
    # Standalone preview of just this page against the real database.
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