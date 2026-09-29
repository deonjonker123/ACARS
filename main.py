"""
main.py

The real application entry point. Loads the theme, shows the startup
splash (ui/splash.py), opens the database, builds the main window and
plugs in the pages - reporting each step on the splash. The splash hands
over to the main window once both maps have loaded (see ui/splash.py).

If a step fails, an error box says which one and why, and the app exits.

Run this (not the individual ui/*.py files) - it's the actual thing
you'll be launching day to day.
"""

import sys
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.theme import load_fonts, build_stylesheet
from ui.splash import SplashScreen
from ui.main_window import MainWindow
from ui.pages.logbook_page import LogbookPage
from ui.pages.home_page import HomePage
from ui.pages.live_map_page import LiveMapPage
from ui.pages.flight_plan_page import FlightPlanPage
from ui.pages.aircraft_page import AircraftPage
from ui.pages.ranks_page import RanksPage
from ui.pages.settings_page import SettingsPage
from core.db import FlightDatabase


def main():
    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    splash = SplashScreen()
    splash.show()
    current = "Starting up"

    try:
        current = "Opening database"
        splash.step(f"{current}...", 0.05)
        db = FlightDatabase()
        db.init_db()

        current = "Building window"
        splash.step(f"{current}...", 0.15)
        window = MainWindow(pilot_data=db.get_pilot())

        pages = [
            ("home", "Dashboard", "Virtual Aviation Gumph", lambda: HomePage(db)),
            ("live_map", "Live Map", "Active Flight", lambda: LiveMapPage(db)),
            ("flight_plan", "Flight Plan", "Dispatched Flight", lambda: FlightPlanPage()),
            ("logbook", "Logbook", "Flight History", lambda: LogbookPage(db)),
            ("aircraft", "Aircraft", "Your Fleet", lambda: AircraftPage(db)),
            ("ranks", "Ranks", "Career Progression", lambda: RanksPage(db)),
            ("settings", "Settings", "Pilot Profile", lambda: SettingsPage(db)),
        ]
        for i, (key, title, subtitle, build) in enumerate(pages):
            current = f"Loading {title}"
            splash.step(f"{current}...", 0.2 + 0.7 * i / len(pages))
            window.add_page(key, title, subtitle, build())

        window.navigate_to("home")
    except Exception as e:
        traceback.print_exc()
        splash.close()
        QMessageBox.critical(
            None, "ACARS - Startup Failed",
            f"Startup failed while: {current.lower()}.\n\n{e}",
        )
        sys.exit(1)

    maps = [window.get_page("home").history_map, window.get_page("live_map").map]
    splash.finish(window, maps)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()