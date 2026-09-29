"""
core/paths.py

Where the app's files live - the same whether it runs from PyCharm
(python main.py) or as the built Tailwind.exe (build.py / PyInstaller).

Two kinds of files:
  - Bundled, read-only: assets/ (fonts, badges, logo, icon, map) and
    data/fleet_and_ranks.json + data/airports.csv. They sit next to the
    code - the project folder, or inside the .exe's folder once built -
    so the modules that read them keep finding them relative to their own
    file, as they always have. RESOURCE_DIR points there.
  - The pilot's own data, which must survive rebuilds: the database
    (logbook, fleet, profile), uploaded aircraft photos and the log file.
    These live in USER_DATA_DIR - %LOCALAPPDATA%\\Tailwind ACARS on
    Windows - for both ways of running the app, so there's one logbook.

migrate_legacy_data() is called once at startup (main.py): if the data
folder has no database yet but the project folder still has the old
data/acars.db (and assets/aircraft/ photos), they're COPIED across. The
originals are left where they were, as a backup.

Usage:
    from core.paths import DB_PATH, AIRCRAFT_IMAGE_DIR, LOG_PATH, ICON_PATH
"""

import os
import shutil
import sqlite3
import sys

APP_NAME = "Tailwind"
DATA_FOLDER_NAME = "Tailwind ACARS"
APP_USER_MODEL_ID = "Tailwind.ACARS"

FROZEN = bool(getattr(sys, "frozen", False))

RESOURCE_DIR = (
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    if FROZEN else
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
ICON_PATH = os.path.join(RESOURCE_DIR, "assets", "app_icon.png")


def _user_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, DATA_FOLDER_NAME)


USER_DATA_DIR = _user_data_dir()
DB_PATH = os.path.join(USER_DATA_DIR, "acars.db")
AIRCRAFT_IMAGE_DIR = os.path.join(USER_DATA_DIR, "aircraft")
LOG_PATH = os.path.join(USER_DATA_DIR, "acars.log")

_LEGACY_DB_PATH = os.path.join(RESOURCE_DIR, "data", "acars.db")
_LEGACY_IMAGE_DIR = os.path.join(RESOURCE_DIR, "assets", "aircraft")


def ensure_user_data_dir():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    os.makedirs(AIRCRAFT_IMAGE_DIR, exist_ok=True)


def migrate_legacy_data():
    """First run only: copies the old project-folder database and aircraft
    photos into USER_DATA_DIR. Returns a list of what was copied (empty if
    there was nothing to do). The originals are not touched."""
    ensure_user_data_dir()
    if os.path.exists(DB_PATH) or not os.path.exists(_LEGACY_DB_PATH):
        return []

    copied = []

    source = sqlite3.connect(_LEGACY_DB_PATH)
    try:
        target = sqlite3.connect(DB_PATH)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    copied.append(f"{_LEGACY_DB_PATH} -> {DB_PATH}")

    if os.path.isdir(_LEGACY_IMAGE_DIR):
        for name in os.listdir(_LEGACY_IMAGE_DIR):
            src = os.path.join(_LEGACY_IMAGE_DIR, name)
            dst = os.path.join(AIRCRAFT_IMAGE_DIR, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                copied.append(f"{src} -> {dst}")
    return copied


if __name__ == "__main__":
    print(f"Frozen (running as .exe): {FROZEN}")
    print(f"Bundled files:  {RESOURCE_DIR}")
    print(f"Your data:      {USER_DATA_DIR}")
    print(f"  database:     {DB_PATH}  (exists: {os.path.exists(DB_PATH)})")
    print(f"  photos:       {AIRCRAFT_IMAGE_DIR}")
    print(f"  log:          {LOG_PATH}")
    print(f"Icon:           {ICON_PATH}  (exists: {os.path.exists(ICON_PATH)})")
    print(f"Old database:   {_LEGACY_DB_PATH}  (exists: {os.path.exists(_LEGACY_DB_PATH)})")