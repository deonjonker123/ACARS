"""
main.py

The real application entry point. Loads the theme, shows the startup
splash (ui/splash.py), prepares the data folder, opens the database,
builds the main window and plugs in the pages - reporting each step on
the splash. The splash hands over to the main window once both maps have
loaded (see ui/splash.py).

If a step fails, an error box says which one and why, and the app exits.

Also sets up the Windows side of things: the app icon (window + taskbar)
and a taskbar ID, so Windows groups and pins the app as Tailwind rather
than as Python. When running as the built Tailwind.exe there's no console,
so everything the app prints goes to acars.log in the data folder
(core/paths.py) instead.

Run this (not the individual ui/*.py files) - it's the actual thing
you'll be launching day to day, or build it with build.py.
"""

import os
import sys
import traceback
from datetime import datetime

from core.paths import (
    APP_NAME, APP_USER_MODEL_ID, FROZEN, ICON_PATH, LOG_PATH,
    ensure_user_data_dir, migrate_legacy_data,
)
from version import APP_VERSION

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon

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

LOG_MAX_BYTES = 1_000_000


def _log_to_file():
    """The .exe has no console: send prints and errors to acars.log. The
    previous log is kept as acars.log.1 once it passes LOG_MAX_BYTES."""
    ensure_user_data_dir()
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = log
    sys.stderr = log


def _set_taskbar_id():
    """Windows only: without this the taskbar shows Python's icon and
    groups the app with other Python programs."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception as e:
            print(f"[main] Couldn't set the taskbar ID: {e}")


def main():
    if FROZEN:
        _log_to_file()
    print(f"--- {APP_NAME} {APP_VERSION} started {datetime.now():%Y-%m-%d %H:%M:%S} ---")

    _set_taskbar_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    splash = SplashScreen()
    splash.show()
    current = "Starting up"

    try:
        current = "Preparing data"
        splash.step(f"{current}...", 0.02)
        for line in migrate_legacy_data():
            print(f"[main] Copied {line}")

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
            None, f"{APP_NAME} - Startup Failed",
            f"Startup failed while: {current.lower()}.\n\n{e}",
        )
        sys.exit(1)

    maps = [window.get_page("home").history_map, window.get_page("live_map").map]
    splash.finish(window, maps)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()