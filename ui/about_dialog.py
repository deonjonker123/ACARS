"""
ui/about_dialog.py

The About box, opened by clicking the version number at the bottom of the
sidebar (ui/main_window.py): app name and version, where the pilot's data
lives, links to the source code and releases, and the licence and
third-party notices (THIRD_PARTY_NOTICES.txt, bundled with the app).

Usage:
    AboutDialog(parent).exec()
"""

import os
import sys

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.theme import PALETTE, font, font_heading, font_label
from core.paths import APP_NAME, ICON_PATH, RESOURCE_DIR, USER_DATA_DIR, ensure_user_data_dir
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
        self.resize(620, 560)
        self.setStyleSheet(f"""
            QDialog#AboutDialog {{ background-color: {PALETTE['bg_panel']}; }}
            QTextEdit {{
                background-color: {PALETTE['bg']};
                color: {PALETTE['text_secondary']};
                border: 1px solid {PALETTE['border']};
                border-radius: 4px;
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


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    AboutDialog().exec()