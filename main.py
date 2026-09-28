"""
main.py

The real application entry point. Loads the theme, opens the database,
builds the main window, and plugs in whichever pages exist so far.

Run this (not the individual ui/*.py files) once the app has more than
one real page - it's the actual thing you'll be launching day to day.
"""

import sys
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from ui.theme import PALETTE, load_fonts, build_stylesheet, font
from ui.main_window import MainWindow
from ui.pages.logbook_page import LogbookPage
from ui.pages.home_page import HomePage
from core.db import FlightDatabase


def _placeholder(text):
    """Stand-in for pages that don't exist yet (Home, Aircraft)."""
    w = QWidget()
    layout = QVBoxLayout(w)
    label = QLabel(text)
    label.setFont(font(14))
    label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
    layout.addWidget(label, alignment=Qt.AlignCenter)
    return w


def main():
    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    db = FlightDatabase()
    db.init_db()

    window = MainWindow(pilot_data=db.get_pilot())

    window.add_page("home", "Dashboard", "Virtual Aviation Gumph", HomePage(db))
    window.add_page("logbook", "Logbook", "Flight History", LogbookPage(db))
    window.add_page("aircraft", "Aircraft", "Your Fleet", _placeholder("Aircraft page - coming soon"))

    window.navigate_to("home")
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()