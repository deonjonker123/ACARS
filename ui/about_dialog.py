"""
ui/about_dialog.py

The About box, opened by clicking the version number at the bottom of the
sidebar (ui/main_window.py): app name and version, where the pilot's data
lives, backup and restore of that data (core/backup.py), links to the
source code and releases, and the licence and third-party notices
(THIRD_PARTY_NOTICES.txt, bundled with the app).

Restore is blocked while a flight is dispatched or waiting to be submitted
(active_flight.json exists), and restarts the app when it's done.

Usage:
    AboutDialog(parent).exec()
"""

import os
import sys
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout,
)
from PySide6.QtCore import QProcess, QSettings, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.theme import PALETTE, font, font_heading, font_label
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


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setObjectName("AboutDialog")
        self.resize(620, 600)
        self.setStyleSheet(f"""
            QDialog#AboutDialog {{ background-color: {PALETTE['bg_panel']}; }}
            QTextEdit {{
                background-color: {PALETTE['bg']};
                color: {PALETTE['text_secondary']};
                border: 1px solid {PALETTE['border']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(16)
        icon = QLabel()
        pixmap = QPixmap(ICON_PATH)
        if not pixmap.isNull():
            icon.setPixmap(pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header.addWidget(icon, alignment=Qt.AlignTop)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        name = QLabel(f"{APP_NAME} ACARS")
        name.setFont(font_heading(22))
        titles.addWidget(name)
        version = QLabel(f"Version {APP_VERSION}")
        version.setFont(font_label(10))
        version.setStyleSheet(f"color: {PALETTE['accent']};")
        titles.addWidget(version)
        links = QLabel(
            f'<a href="{SOURCE_URL}" style="color:{PALETTE["accent"]};">Source code</a>'
            f'&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'<a href="{RELEASES_PAGE}" style="color:{PALETTE["accent"]};">Releases</a>'
        )
        links.setFont(font(10))
        links.setOpenExternalLinks(True)
        titles.addWidget(links)
        header.addLayout(titles, stretch=1)
        layout.addLayout(header)

        licence = QLabel(
            "Free software under the GNU Affero General Public License v3.0, "
            "with no warranty. Third-party components keep their own licences - see below."
        )
        licence.setFont(font(9))
        licence.setWordWrap(True)
        licence.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(licence)

        data_row = QHBoxLayout()
        data_label = QLabel(f"Your data: {USER_DATA_DIR}")
        data_label.setFont(font(9))
        data_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        data_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        data_row.addWidget(data_label, stretch=1)
        open_data = QPushButton("Open Data Folder")
        open_data.setCursor(Qt.PointingHandCursor)
        open_data.clicked.connect(self._open_data_folder)
        data_row.addWidget(open_data)
        layout.addLayout(data_row)

        backup_row = QHBoxLayout()
        backup_label = QLabel("Logbook, fleet, profile and aircraft photos in one .zip file.")
        backup_label.setFont(font(9))
        backup_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        backup_row.addWidget(backup_label, stretch=1)
        back_up = QPushButton("Back Up...")
        back_up.setCursor(Qt.PointingHandCursor)
        back_up.clicked.connect(self._back_up)
        backup_row.addWidget(back_up)
        restore = QPushButton("Restore...")
        restore.setCursor(Qt.PointingHandCursor)
        restore.clicked.connect(self._restore)
        backup_row.addWidget(restore)
        layout.addLayout(backup_row)

        notices = QTextEdit()
        notices.setReadOnly(True)
        notices.setFont(font(9))
        notices.setPlainText(_notices_text())
        layout.addWidget(notices, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _open_data_folder(self):
        ensure_user_data_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(USER_DATA_DIR))

    def _back_up(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Back Up Tailwind", os.path.join(_backup_folder(), default_backup_name()),
            "Tailwind backup (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            manifest = create_backup(path)
        except (BackupError, OSError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Backup Failed", str(e))
            return
        QApplication.restoreOverrideCursor()
        _remember_backup_folder(path)
        QMessageBox.information(
            self, "Backed Up",
            f"{manifest['flights']} flights and {manifest['aircraft']} aircraft backed up to:\n\n{path}")

    def _restore(self):
        if os.path.exists(ACTIVE_FLIGHT_PATH):
            QMessageBox.information(
                self, "Flight in Progress",
                "A flight is dispatched or waiting to be submitted. Finish it, submit or "
                "discard it, or cancel the dispatch before restoring a backup.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Restore Tailwind Backup", _backup_folder(),
                                              "Tailwind backup (*.zip)")
        if not path:
            return
        try:
            info = read_backup(path)
        except (BackupError, OSError) as e:
            QMessageBox.warning(self, "Can't Restore", str(e))
            return
        _remember_backup_folder(path)

        try:
            made = datetime.fromisoformat(info["created"]).strftime("%d %b %Y, %H:%M")
        except (KeyError, TypeError, ValueError):
            made = "an unknown date"
        answer = QMessageBox.question(
            self, "Restore Backup?",
            f"Backup from {made}, made with Tailwind {info.get('app_version', '?')}:\n"
            f"{info['flights']} flights, {info['aircraft']} aircraft, {info['photos']} photos.\n\n"
            f"This replaces your current logbook, fleet, profile and aircraft photos. "
            f"Your current data is saved to the backups folder first.\n\n"
            f"{APP_NAME} restarts when it's done.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            safety = restore_backup(path)
        except (BackupError, OSError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Restore Failed", str(e))
            return
        QApplication.restoreOverrideCursor()
        saved = f"Your previous data was saved to:\n{safety}\n\n" if safety else ""
        QMessageBox.information(self, "Restored", f"Backup restored.\n\n{saved}{APP_NAME} will now restart.")
        self.accept()
        if not _restart_app():
            QMessageBox.information(None, "Restart Tailwind",
                                    f"Couldn't restart automatically - please start {APP_NAME} again.")
            QApplication.quit()


def _backup_folder():
    """Where the backup file dialogs open: the last folder used, else Documents."""
    default = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    folder = QSettings("Tailwind", "ACARS").value("backup_folder", default)
    return folder if folder and os.path.isdir(folder) else default


def _remember_backup_folder(path):
    QSettings("Tailwind", "ACARS").setValue("backup_folder", os.path.dirname(path))


def _restart_app():
    """Starts a new copy of the app and closes this one. The log file is let
    go first so the new copy can start its own. False if it couldn't start."""
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