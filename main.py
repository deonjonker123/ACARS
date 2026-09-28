"""
main.py

The real application entry point. Loads the theme, opens the database,
builds the main window, and plugs in the pages.

Run this (not the individual ui/*.py files) - it's the actual thing
you'll be launching day to day.
"""

import sys
from PySide6.QtWidgets import QApplication

from ui.theme import load_fonts, build_stylesheet
from ui.main_window import MainWindow
from ui.pages.logbook_page import LogbookPage
from ui.pages.home_page import HomePage
from ui.pages.flight_plan_page import FlightPlanPage
from ui.pages.aircraft_page import AircraftPage
from ui.pages.ranks_page import RanksPage
from ui.pages.settings_page import SettingsPage
from core.db import FlightDatabase


def main():
    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    db = FlightDatabase()
    db.init_db()

    window = MainWindow(pilot_data=db.get_pilot())

    window.add_page("home", "Dashboard", "Virtual Aviation Gumph", HomePage(db))
    window.add_page("flight_plan", "Flight Plan", "Dispatched Flight", FlightPlanPage())
    window.add_page("logbook", "Logbook", "Flight History", LogbookPage(db))
    window.add_page("aircraft", "Aircraft", "Your Fleet", AircraftPage(db))
    window.add_page("ranks", "Ranks", "Career Progression", RanksPage(db))
    window.add_page("settings", "Settings", "Pilot Profile", SettingsPage(db))

    window.navigate_to("home")
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()