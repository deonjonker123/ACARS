import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.paths import AIRCRAFT_IMAGE_DIR, DB_PATH, USER_DATA_DIR, ensure_user_data_dir
from core.updates import is_newer
from version import APP_VERSION

BACKUP_FORMAT = 1
MANIFEST_NAME = "manifest.json"
DB_NAME = "acars.db"
PHOTO_FOLDER = "aircraft"
REQUIRED_TABLES = {"aircraft", "pilot", "flights"}
PRE_RESTORE_DIR = os.path.join(USER_DATA_DIR, "backups")


class BackupError(Exception):
    """The backup can't be made or used. The message is shown to the pilot."""


def default_backup_name():
    return f"Tailwind-backup-{datetime.now():%Y-%m-%d}.zip"


def _snapshot_db(source_path, target_path):
    source = sqlite3.connect(source_path)
    try:
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _count(conn, sql):
    try:
        return conn.execute(sql).fetchone()[0]
    except sqlite3.Error:
        return None


def _db_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        aircraft = _count(conn, "SELECT COUNT(*) FROM aircraft WHERE retired = 0")
        if aircraft is None:
            aircraft = _count(conn, "SELECT COUNT(*) FROM aircraft")
        return {"flights": _count(conn, "SELECT COUNT(*) FROM flights"), "aircraft": aircraft}
    finally:
        conn.close()


def create_backup(zip_path):
    """Writes a backup of the current data to zip_path. Returns the manifest."""
    ensure_user_data_dir()
    if not os.path.exists(DB_PATH):
        raise BackupError("There's no database to back up yet.")

    with tempfile.TemporaryDirectory() as work:
        snapshot = os.path.join(work, DB_NAME)
        _snapshot_db(DB_PATH, snapshot)
        manifest = {
            "format": BACKUP_FORMAT,
            "app_version": APP_VERSION,
            "created": datetime.now().isoformat(timespec="seconds"),
            **_db_counts(snapshot),
        }

        partial = zip_path + ".partial"
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
                zf.write(snapshot, DB_NAME)
                if os.path.isdir(AIRCRAFT_IMAGE_DIR):
                    for name in sorted(os.listdir(AIRCRAFT_IMAGE_DIR)):
                        path = os.path.join(AIRCRAFT_IMAGE_DIR, name)
                        if os.path.isfile(path):
                            zf.write(path, f"{PHOTO_FOLDER}/{name}")
            os.replace(partial, zip_path)
        except OSError as e:
            if os.path.exists(partial):
                os.remove(partial)
            raise BackupError(f"Couldn't write the backup: {e}") from e
    return manifest


def _photo_names(zf):
    photos = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        folder, _, name = info.filename.partition("/")
        if folder == PHOTO_FOLDER and name and name == os.path.basename(name) and name not in (".", ".."):
            photos.append((info.filename, name))
    return photos


def _check_db(db_path):
    conn = sqlite3.connect(db_path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"The backup's database is damaged ({result}).")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as e:
        raise BackupError(f"The backup's database can't be read ({e}).") from e
    finally:
        conn.close()
    missing = REQUIRED_TABLES - tables
    if missing:
        raise BackupError(f"The backup's database is missing: {', '.join(sorted(missing))}.")


def _open_backup(zip_path, work):
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise BackupError(f"That isn't a Tailwind backup (can't open it as a zip: {e}).") from e
    with zf:
        names = set(zf.namelist())
        if MANIFEST_NAME not in names or DB_NAME not in names:
            raise BackupError("That isn't a Tailwind backup (no manifest or database inside).")
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise BackupError("The backup's manifest can't be read.") from e
        if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
            raise BackupError("This backup was made in a format this version of Tailwind doesn't know.")
        made_by = manifest.get("app_version")
        if is_newer(made_by, APP_VERSION):
            raise BackupError(f"This backup was made by Tailwind {made_by}. "
                              f"Update Tailwind (you have {APP_VERSION}) before restoring it.")

        db_path = os.path.join(work, DB_NAME)
        with zf.open(DB_NAME) as src, open(db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        _check_db(db_path)
        return manifest, db_path, _photo_names(zf)


def read_backup(zip_path):
    """The backup's manifest, after checking it can be restored. Raises
    BackupError otherwise."""
    with tempfile.TemporaryDirectory() as work:
        manifest, db_path, photos = _open_backup(zip_path, work)
        return {**manifest, **_db_counts(db_path), "photos": len(photos)}


def restore_backup(zip_path):
    """Replaces the current data with the backup's. Saves the current data
    first and returns that file's path (None if there was nothing to save)."""
    ensure_user_data_dir()
    with tempfile.TemporaryDirectory() as work:
        _, db_path, photos = _open_backup(zip_path, work)

        safety = None
        if os.path.exists(DB_PATH):
            os.makedirs(PRE_RESTORE_DIR, exist_ok=True)
            safety = os.path.join(PRE_RESTORE_DIR, f"pre-restore-{datetime.now():%Y-%m-%d_%H%M%S}.zip")
            create_backup(safety)

        try:
            _snapshot_db(db_path, DB_PATH)
        except sqlite3.Error as e:
            raise BackupError(f"Couldn't write the database ({e}). "
                              f"Your previous data is saved in {safety}.") from e

        for name in os.listdir(AIRCRAFT_IMAGE_DIR):
            path = os.path.join(AIRCRAFT_IMAGE_DIR, name)
            if os.path.isfile(path):
                os.remove(path)
        with zipfile.ZipFile(zip_path) as zf:
            for archive_name, name in photos:
                with zf.open(archive_name) as src, open(os.path.join(AIRCRAFT_IMAGE_DIR, name), "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return safety


if __name__ == "__main__":
    target = os.path.join(tempfile.gettempdir(), default_backup_name())
    print("Backing up to", target)
    print(create_backup(target))
    print("Reads back as:", read_backup(target))