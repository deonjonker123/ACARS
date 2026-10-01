"""
ui/about_dialog.py

The About box, opened by clicking the version number at the bottom of the
sidebar (ui/main_window.py): app name and version, where the pilot's data
lives, backup and restore of that data (core/backup.py), links to the
source code and releases, and the licence and third-party notices
(THIRD_PARTY_NOTICES.txt, bundled with the app). Same frameless style as
every other dialog (ui/widgets/dialogs.py).

Restore is blocked while a flight is dispatched or waiting to be submitted
(active_flight.json exists), and restarts the app when it's done.

Usage:
    AboutDialog(parent).exec()
"""

import os
import sys
from datetime import datetime

from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout
from PySide6.QtCore import QProcess, QSettings, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.theme import PALETTE, font, font_label
from ui.widgets.dialogs import (
    ThemedDialog, dialog_button, inform, confirm, NEUTRAL, DANGER, POSITIVE,
)
from core.paths import (
    ACTIVE_FLIGHT_PATH, APP_NAME, FROZEN, ICON_PATH, RESOURCE_DIR, USER_DATA_DIR, ensure_user_data_dir,
)
from core.backup import BackupError, create_backup, default_backup_name, read_backup, restore_backup
from core.updates import RELEASES_PAGE
from version import APP_VERSION

SOURCE_URL = "https://github.com/deonjonker123/ACARS"
NOTICES_PATH = os.path.join(RESOURCE_DIR, "THIRD_PARTY_NOTICES.txt")


def _notices_text():
    try:
        with open(NOTICES_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return (f"{APP_NAME} ACARS is free software under the GNU Affero General Public "
                f"License v3.0.\n\nThe full notices file wasn't found - see {SOURCE_URL}")


def _small_label(text, color="text_secondary"):
    label = QLabel(text)
    label.setFont(font(9))
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {PALETTE[color]}; border: none;")
    return label


class AboutDialog(ThemedDialog):
    def __init__(self, parent=None):
        super().__init__(parent, f"{APP_NAME} ACARS", width=640)
        self.resize(640, 640)
        self.setStyleSheet(self.styleSheet() + f"""
            QTextEdit {{
                background-color: {PALETTE['bg_panel']};
                color: {PALETTE['text_secondary']};
                border: 1px solid {PALETTE['border']};
                border-radius: 0px;
            }}
        """)

        header = QHBoxLayout()
        header.setSpacing(16)
        icon = QLabel()
        pixmap = QPixmap(ICON_PATH)
        if not pixmap.isNull():
            icon.setPixmap(pixmap.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setStyleSheet("border: none;")
        header.addWidget(icon, alignment=Qt.AlignTop)

        titles = QVBoxLayout()
        titles.setSpacing(4)
        version = QLabel(f"VERSION {APP_VERSION}")
        version.setFont(font_label(10))
        version.setStyleSheet(f"color: {PALETTE['accent']}; border: none;")
        titles.addWidget(version)
        links = QLabel(
            f'<a href="{SOURCE_URL}" style="color:{PALETTE["accent"]};">Source code</a>'
            f'&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'<a href="{RELEASES_PAGE}" style="color:{PALETTE["accent"]};">Releases</a>'
        )
        links.setFont(font(10))
        links.setOpenExternalLinks(True)
        links.setStyleSheet("border: none;")
        titles.addWidget(links)
        titles.addWidget(_small_label(
            "Free software under the GNU Affero General Public License v3.0, "
            "with no warranty. Third-party components keep their own licences - see below."))
        header.addLayout(titles, stretch=1)
        self.body.addLayout(header)

        data_row = QHBoxLayout()
        data_row.setSpacing(12)
        data_label = _small_label(f"Your data: {USER_DATA_DIR}")
        data_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        data_row.addWidget(data_label, stretch=1)
        open_data = dialog_button("Open Data Folder", NEUTRAL)
        open_data.clicked.connect(self._open_data_folder)
        data_row.addWidget(open_data)
        self.body.addLayout(data_row)

        backup_row = QHBoxLayout()
        backup_row.setSpacing(12)
        backup_row.addWidget(_small_label("Logbook, fleet, profile and aircraft photos in one .zip file."),
                             stretch=1)
        back_up = dialog_button("Back Up...", POSITIVE)
        back_up.clicked.connect(self._back_up)
        backup_row.addWidget(back_up)
        restore = dialog_button("Restore...", DANGER)
        restore.clicked.connect(self._restore)
        backup_row.addWidget(restore)
        self.body.addLayout(backup_row)

        notices = QTextEdit()
        notices.setReadOnly(True)
        notices.setFont(font(9))
        notices.setPlainText(_notices_text())
        self.body.addWidget(notices, stretch=1)

        buttons = self.add_buttons([("close", "Close", NEUTRAL)], default="close")
        buttons["close"].clicked.connect(self.accept)

    def _open_data_folder(self):
        ensure_user_data_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(USER_DATA_DIR))

    def _back_up(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Back Up Flyt", os.path.join(_backup_folder(), default_backup_name()),
            "Flyt backup (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            manifest = create_backup(path)
        except (BackupError, OSError) as e:
            QApplication.restoreOverrideCursor()
            inform(self, "Backup Failed", str(e))
            return
        QApplication.restoreOverrideCursor()
        _remember_backup_folder(path)
        inform(self, "Backed Up",
               f"{manifest['flights']} flights and {manifest['aircraft']} aircraft backed up to:",
               details=path)

    def _restore(self):
        if os.path.exists(ACTIVE_FLIGHT_PATH):
            inform(self, "Flight in Progress",
                   "A flight is dispatched or waiting to be submitted. Finish it, submit or "
                   "discard it, or cancel the dispatch before restoring a backup.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Restore Flyt Backup", _backup_folder(),
                                              "Flyt backup (*.zip)")
        if not path:
            return
        try:
            info = read_backup(path)
        except (BackupError, OSError) as e:
            inform(self, "Can't Restore", str(e))
            return
        _remember_backup_folder(path)

        try:
            made = datetime.fromisoformat(info["created"]).strftime("%d %b %Y, %H:%M")
        except (KeyError, TypeError, ValueError):
            made = "an unknown date"
        if not confirm(
                self, "Restore Backup",
                f"This replaces your current logbook, fleet, profile and aircraft photos. "
                f"Your current data is saved to the backups folder first, and {APP_NAME} "
                f"restarts when it's done.",
                "Restore",
                details=f"Backup from {made}, made with Flyt {info.get('app_version', '?')}\n"
                        f"{info['flights']} flights  ·  {info['aircraft']} aircraft  ·  {info['photos']} photos"):
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            safety = restore_backup(path)
        except (BackupError, OSError) as e:
            QApplication.restoreOverrideCursor()
            inform(self, "Restore Failed", str(e))
            return
        QApplication.restoreOverrideCursor()
        inform(self, "Restored", f"Backup restored. {APP_NAME} will now restart.",
               details=f"Your previous data was saved to:\n{safety}" if safety else None)
        self.accept()
        if not _restart_app():
            inform(None, "Restart Flyt", f"Couldn't restart automatically - please start {APP_NAME} again.")
            QApplication.quit()


def _backup_folder():
    """Where the backup file dialogs open: the last folder used, else Documents."""
    default = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    folder = QSettings("Flyt", "ACARS").value("backup_folder", default)
    return folder if folder and os.path.isdir(folder) else default


def _remember_backup_folder(path):
    QSettings("Flyt", "ACARS").setValue("backup_folder", os.path.dirname(path))


def _restart_app():
    """Starts a new copy of the app and closes this one (without the exit
    confirmation). The log file is let go first so the new copy can start
    its own. False if it couldn't start."""
    if FROZEN:
        program, args = sys.executable, sys.argv[1:]
    else:
        program, args = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    log = sys.stdout
    if log is not None and log is not sys.__stdout__:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        try:
            log.close()
        except OSError:
            pass
    for window in QApplication.topLevelWidgets():
        if hasattr(window, "confirm_on_close"):
            window.confirm_on_close = False
    started, _ = QProcess.startDetached(program, args, os.getcwd())
    if started:
        QApplication.quit()
    return started


if __name__ == "__main__":
    from ui.theme import load_fonts, build_stylesheet

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    AboutDialog().exec()