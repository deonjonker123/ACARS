"""
ui/main_window.py

The app shell: sidebar (logo + nav, with Settings pinned to the bottom
below a divider), a header showing the current page's title/subtitle on
the left and pilot info (name, rank + badge, total hours, current
location) on the right, and a stacked content area that pages plug into
via add_page().

Usage:
    window = MainWindow(pilot_data=db.get_pilot())
    window.add_page("home", "Home", "", home_page_widget)
    window.add_page("logbook", "Logbook", "FLIGHT HISTORY", logbook_widget)
    window.navigate_to("home")
    window.show()
"""

import os
import sys
import threading
from datetime import datetime, timezone
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QProgressBar
)

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QSize
from PySide6.QtGui import QPixmap, QFont

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.theme import PALETTE, GRADE_COLORS, load_fonts, font, font_heading, font_label, garamond, logo_exists, LOGO_PATH
from core.pilot import PilotProgress
from version import APP_VERSION
from core.paths import APP_NAME, ACTIVE_FLIGHT_PATH
from ui.widgets.dialogs import confirm
from core.updates import check_for_update
from ui.about_dialog import AboutDialog
from ui.icons import make_icon, NAV_ICONS
from core.landing_grade import letter_for

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSETS_DIR = os.path.join(_PROJECT_ROOT, "assets")

SIDEBAR_WIDTH = 320
NAV_ICON_SIZE = 18

class _UpdateNotifier(QObject):
    """Carries the result of the background update check (core/updates.py)
    back to the UI thread: the release dict, or None if nothing newer."""
    found = Signal(object)


def _check_for_update_in_background(notifier):
    def run():
        try:
            notifier.found.emit(check_for_update(APP_VERSION))
        except RuntimeError:
            pass
    threading.Thread(target=run, name="UpdateCheck", daemon=True).start()

class MainWindow(QMainWindow):
    def __init__(self, pilot_data=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.confirm_on_close = True

        self.pilot_data = pilot_data or {}
        self._nav_buttons = {}
        self._nav_icons = {}
        self._page_titles = {}

        central = QWidget()
        central.setObjectName("MainPanel")
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_sidebar())

        right_side = QVBoxLayout()
        right_side.setContentsMargins(0, 0, 0, 0)
        right_side.setSpacing(0)
        right_side.addWidget(self._build_header())

        self.content_stack = QStackedWidget()
        right_side.addWidget(self.content_stack)

        right_container = QWidget()
        right_container.setLayout(right_side)
        root_layout.addWidget(right_container)

        self._update_notifier = _UpdateNotifier(self)
        self._update_notifier.found.connect(self._show_update)
        _check_for_update_in_background(self._update_notifier)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 20, 0, 16)
        layout.setSpacing(4)

        logo_container = QWidget()
        logo_layout = QVBoxLayout(logo_container)
        logo_layout.setContentsMargins(20, 0, 20, 20)
        if logo_exists():
            pixmap = QPixmap(LOGO_PATH)
            logo_label = QLabel()
            logo_label.setPixmap(pixmap.scaledToHeight(40, Qt.SmoothTransformation))
            logo_layout.addWidget(logo_label)
        else:
            logo_label = QLabel("ACARS")
            logo_label.setFont(font_heading(20))
            logo_label.setStyleSheet(f"color: {PALETTE['text_primary']};")
            logo_layout.addWidget(logo_label)
        layout.addWidget(logo_container)
        self.utc_clock = QLabel()
        self.utc_clock.setFont(font_label(11))
        self.utc_clock.setAlignment(Qt.AlignCenter)
        self.utc_clock.setToolTip("Current time, UTC (Zulu)")
        self.utc_clock.setContentsMargins(0, 0, 0, 12)
        layout.addWidget(self.utc_clock)
        self._update_clock()
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

        self.nav_items = [
            ("home", "Dashboard"),
            ("profile", "Profile"),
            ("live_map", "Live Map"),
            ("flight_plan", "Flight Plan"),
            ("logbook", "Logbook"),
            ("aircraft", "Aircraft"),
            ("ranks", "Ranks"),
        ]
        for key, label in self.nav_items:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("NavItem")
            btn.setProperty("active", False)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(font_label(11))
            btn.clicked.connect(lambda checked=False, k=key: self.navigate_to(k))
            layout.addWidget(btn)
            self._nav_buttons[key] = btn
            self._add_nav_icon(key, btn)

        layout.addStretch()
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {PALETTE['border']}; border: none;")
        layout.addWidget(divider)
        layout.addSpacing(8)

        settings_btn = QPushButton("  Settings")
        settings_btn.setObjectName("NavItem")
        settings_btn.setProperty("active", False)
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setFont(font_label(11))
        settings_btn.clicked.connect(lambda checked=False: self.navigate_to("settings"))
        layout.addWidget(settings_btn)
        self._nav_buttons["settings"] = settings_btn
        self._add_nav_icon("settings", settings_btn)

        layout.addSpacing(8)
        version_button = QPushButton(f"v{APP_VERSION}")
        version_button.setFont(font_label(8))
        version_button.setCursor(Qt.PointingHandCursor)
        version_button.setToolTip(f"About {APP_NAME}")
        version_button.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none; padding: 0;
                               color: {PALETTE['text_muted']}; }}
                QPushButton:hover {{ color: {PALETTE['accent']}; }}
            """)
        version_button.clicked.connect(lambda checked=False: AboutDialog(self).exec())
        layout.addWidget(version_button, alignment=Qt.AlignCenter)

        self.update_label = QLabel("")
        self.update_label.setFont(font_label(8))
        self.update_label.setAlignment(Qt.AlignCenter)
        self.update_label.setOpenExternalLinks(True)
        self.update_label.setVisible(False)
        layout.addWidget(self.update_label)

        return sidebar

    def _add_nav_icon(self, key, button):
        """The nav item's icon: grey normally, gold on the active page
        (swapped in navigate_to)."""
        name = NAV_ICONS[key]
        self._nav_icons[key] = (
            make_icon(name, PALETTE["text_secondary"], NAV_ICON_SIZE),
            make_icon(name, PALETTE["accent"], NAV_ICON_SIZE),
        )
        button.setIcon(self._nav_icons[key][0])
        button.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))

    def _update_clock(self):
        now = datetime.now(timezone.utc)
        self.utc_clock.setText(
            f'<span style="color:{PALETTE["text_primary"]};">{now:%H:%M:%S}</span>'
            f'&nbsp;<span style="color:{PALETTE["text_muted"]};">Z</span>'
        )

    def _show_update(self, release):
        """Shows the "Update available" link under the version, if the
        background check found a newer release."""
        if not release:
            return
        self.update_label.setText(
            f'<a href="{release["url"]}" style="color:{PALETTE["accent"]}; text-decoration:none;">'
            f'Update available: v{release["version"]}</a>'
        )
        self.update_label.setToolTip("Opens the release page to download it")
        self.update_label.setVisible(True)

    def _build_header(self):
        header = QWidget()
        header.setObjectName("Header")
        header.setFixedHeight(90)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(32, 16, 32, 16)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addStretch()

        self.header_title = QLabel("")
        self.header_title.setObjectName("PageTitle")
        self.header_title.setFont(font_heading(26))
        title_col.addWidget(self.header_title)

        self.header_subtitle = QLabel("")
        self.header_subtitle.setObjectName("PageSubtitle")
        self.header_subtitle.setFont(font_label(10))
        title_col.addWidget(self.header_subtitle)
        title_col.addStretch()

        layout.addLayout(title_col)
        layout.addStretch()
        layout.addWidget(self._build_pilot_info())

        return header

    def _build_pilot_info(self):
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        name_col = QVBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(2)

        name = self.pilot_data.get("name") or "Unnamed Pilot"

        name_label = QLabel(name)
        name_label.setObjectName("PilotName")
        name_label.setFont(garamond(22, QFont.Bold))
        name_label.setStyleSheet(
            f"color: {PALETTE['text_primary']};"
        )
        name_col.addWidget(name_label)

        rank_row = QHBoxLayout()
        rank_row.setContentsMargins(0, 0, 0, 0)
        rank_row.setSpacing(6)

        rank_label = QLabel(
            self.pilot_data.get("rank") or "Student Pilot"
        )
        rank_label.setFont(font_label(10))
        rank_label.setStyleSheet(
            f"color: {PALETTE['accent']};"
        )
        rank_row.addWidget(rank_label)

        badge_path = self._resolve_badge_path(
            self.pilot_data.get("rank_badge_path")
        )

        if badge_path and os.path.exists(badge_path):
            badge_label = QLabel()
            badge_label.setPixmap(
                QPixmap(badge_path).scaled(
                    59,
                    21,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            rank_row.addWidget(badge_label)

        rank_row.addStretch()
        name_col.addLayout(rank_row)

        row.addLayout(name_col)
        progress_col = QVBoxLayout()
        progress_col.setContentsMargins(0, 10, 0, 0)
        progress_col.setSpacing(3)

        total_hours = self.pilot_data.get("total_hours_flown") or 0
        progress = PilotProgress(total_hours)

        bar = QProgressBar()
        bar.setObjectName("RankProgress")
        bar.setTextVisible(False)
        bar.setFixedWidth(120)

        if progress.next_rank:
            current_min = progress.current_rank["min_hours"]
            next_min = progress.next_rank["min_hours"]
            span = next_min - current_min

            fraction = (
                (total_hours - current_min) / span
                if span > 0
                else 1.0
            )

            fraction = max(0.0, min(1.0, fraction))
            bar.setValue(int(fraction * 100))

            caption_text = (
                f"{progress.hours_to_next_rank:.1f} hrs\n"
                f"to {progress.next_rank['name']}"
            )
        else:
            bar.setValue(100)
            caption_text = "Top rank reached"

        progress_col.addWidget(bar)

        progress_caption = QLabel(caption_text)
        progress_caption.setFont(font_label(8))
        progress_caption.setStyleSheet(
            f"color: {PALETTE['text_muted']};"
        )
        progress_col.addWidget(progress_caption)

        row.addLayout(progress_col)
        row.addWidget(self._vdivider())

        hours = self._stat_block(
            f"{total_hours:.1f}",
            "HOURS",
        )
        row.addWidget(hours)
        row.addWidget(self._vdivider())

        flights = self._stat_block(
            f"{self.pilot_data.get('total_completed') or 0:,}",
            "FLIGHTS",
        )
        row.addWidget(flights)
        row.addWidget(self._vdivider())

        avg_landing = self.pilot_data.get("average_landing_rate")
        avg_letter = letter_for(self.pilot_data.get("average_landing_grade"))
        landing_text = f"{avg_landing:,.0f} fpm" if avg_landing is not None else "—"
        if avg_letter:
            landing_text = (f'<span style="color:{GRADE_COLORS[avg_letter]};">{avg_letter}</span>'
                            f' · {landing_text}')
        landing = self._stat_block(landing_text, "AVG LANDING")
        row.addWidget(landing)
        row.addWidget(self._vdivider())

        location = self._stat_block(
            self.pilot_data.get("current_location")
            or self.pilot_data.get("home_airport")
            or "—",
            "LOCATION",
        )
        row.addWidget(location)

        return container

    def _stat_block(self, value, label):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        value_label = QLabel(value)
        value_label.setFont(font_heading(16))
        value_label.setStyleSheet(f"color: {PALETTE['text_primary']};")
        value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(value_label)

        caption = QLabel(label)
        caption.setFont(font_label(8))
        caption.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(caption)

        return block

    def _vdivider(self):
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setStyleSheet(f"color: {PALETTE['border']};")
        line.setFixedHeight(36)
        return line

    def _resolve_badge_path(self, rank_badge_path):
        """rank_badge_path is stored as e.g. 'badges/captain.png' (see
        core/db.py _sync_pilot_rank). Resolve it against assets/."""
        if not rank_badge_path:
            return None
        return os.path.join(_ASSETS_DIR, rank_badge_path)

    def add_page(self, key, title, subtitle, widget):
        self.content_stack.addWidget(widget)
        self._page_titles[key] = (title, subtitle, widget)

    def get_page(self, key):
        """Lets one page reach another to trigger things like a refresh -
        e.g. Home's Save Flight button calling Logbook's refresh() after
        writing a new flight, without the two pages needing to know about
        each other directly."""
        entry = self._page_titles.get(key)
        return entry[2] if entry else None

    def open_debrief(self, flight_id, return_to="logbook"):
        """Shows one flight's debrief (the hidden "debrief" page); its Back
        button returns to the page `return_to`."""
        page = self.get_page("debrief")
        if page is not None and page.show_flight(flight_id, return_to=return_to):
            self.navigate_to("debrief")

    def closeEvent(self, event):
        if self.confirm_on_close:
            text = "Are you sure you want to exit?"
            if os.path.exists(ACTIVE_FLIGHT_PATH):
                text += (f" Your flight in progress is saved - {APP_NAME} will offer to resume it "
                         f"next time you start.")
            if not confirm(self, f"Close {APP_NAME}", text, f"Close {APP_NAME}"):
                event.ignore()
                return
        super().closeEvent(event)

    def navigate_to(self, key):
        if key not in self._page_titles:
            return
        title, subtitle, widget = self._page_titles[key]
        self.header_title.setText(title)
        self.header_subtitle.setText(subtitle.upper() if subtitle else "")
        self.content_stack.setCurrentWidget(widget)

        for k, btn in self._nav_buttons.items():
            btn.setProperty("active", k == key)
            if k in self._nav_icons:
                btn.setIcon(self._nav_icons[k][1 if k == key else 0])
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def update_pilot_data(self, pilot_data):
        """Call after logging a flight to refresh the header's pilot info
        (hours/rank/location change) without rebuilding the whole window."""
        self.pilot_data = pilot_data
        old_widget = self.findChild(QWidget, "Header").layout().itemAt(2).widget()
        new_widget = self._build_pilot_info()
        self.findChild(QWidget, "Header").layout().replaceWidget(old_widget, new_widget)
        old_widget.deleteLater()


if __name__ == "__main__":
    from ui.theme import build_stylesheet

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    dummy_pilot = {
        "name": "Deon Jonker",
        "rank": "Captain",
        "rank_badge_path": "badges/captain.png",
        "total_hours_flown": 137.4,
        "current_location": "KSMO",
    }

    window = MainWindow(pilot_data=dummy_pilot)

    def placeholder(text):
        w = QWidget()
        l = QVBoxLayout(w)
        lbl = QLabel(text)
        lbl.setFont(font(14))
        lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        l.addWidget(lbl, alignment=Qt.AlignCenter)
        return w

    window.add_page("home", "Dashboard", "Virtual Aviation Gumph", placeholder("Home page goes here"))
    window.add_page("live_map", "Live Map", "Active Flight", placeholder("Live Map page goes here"))
    window.add_page("flight_plan", "Flight Plan", "Dispatched Flight", placeholder("Flight Plan page goes here"))
    window.add_page("logbook", "Logbook", "Flight History", placeholder("Logbook page goes here"))
    window.add_page("aircraft", "Aircraft", "Your Fleet", placeholder("Aircraft page goes here"))
    window.add_page("ranks", "Ranks", "Career Progression", placeholder("Ranks page goes here"))
    window.add_page("settings", "Settings", "Pilot Profile", placeholder("Settings page goes here"))
    window.navigate_to("home")

    window.show()
    sys.exit(app.exec())