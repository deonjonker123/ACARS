"""
ui/splash.py

The startup splash: a small borderless window in the app's dark/gold theme
with the Tailwind logo, a progress bar and one line saying what's being
loaded. main.py shows it first, reports each startup step to it, then
hands it the finished main window:

    splash = SplashScreen()
    splash.show()
    splash.step("Opening database...", 0.2)     # text + how far along (0..1)
    ...
    splash.finish(window, maps=[...])           # then app.exec()

finish() keeps the splash up until every map (ui/widgets/map_view.py) has
loaded - or MAP_TIMEOUT_MS has passed, so no internet can't hold the app
up - and until the splash has been visible for at least MIN_DISPLAY_MS.
Then the main window is shown and the splash closes.

The startup work itself blocks the event loop, so step() repaints the
splash straight away rather than waiting for the loop to get to it.
"""

import os
import sys

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QProgressBar
from PySide6.QtCore import Qt, QTimer, QElapsedTimer
from PySide6.QtGui import QPixmap

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.theme import PALETTE, font_heading, font_label, logo_exists, LOGO_PATH

MIN_DISPLAY_MS = 2000
MAP_TIMEOUT_MS = 15000
WIDTH, HEIGHT = 480, 240
LOGO_HEIGHT = 72


class SplashScreen(QWidget):
    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint)
        self.setObjectName("Splash")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setStyleSheet(f"""
            QWidget#Splash {{
                background-color: {PALETTE['bg_panel']};
                border: 1px solid {PALETTE['border']};
            }}
            QProgressBar {{
                background-color: {PALETTE['bg_input']};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {PALETTE['accent']};
                border-radius: 2px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 32)
        layout.setSpacing(0)
        layout.addStretch()

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        if logo_exists():
            logo.setPixmap(QPixmap(LOGO_PATH).scaledToHeight(LOGO_HEIGHT, Qt.SmoothTransformation))
        else:
            logo.setText("ACARS")
            logo.setFont(font_heading(28))
            logo.setStyleSheet(f"color: {PALETTE['text_primary']};")
        layout.addWidget(logo)
        layout.addStretch()

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        layout.addWidget(self.bar)
        layout.addSpacing(12)

        self.label = QLabel("")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(font_label(9))
        self.label.setStyleSheet(f"color: {PALETTE['text_secondary']}; background: transparent;")
        layout.addWidget(self.label)

        self._elapsed = QElapsedTimer()
        self._window = None
        self._maps = []
        self._timed_out = False
        self._closing = False

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def show(self):
        super().show()
        self._elapsed.start()
        self._repaint_now()

    def step(self, text, fraction):
        """Shows the step being loaded and moves the bar to `fraction` (0..1)."""
        self.label.setText(text)
        self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self._repaint_now()

    def finish(self, window, maps=()):
        """Waits for the maps (up to MAP_TIMEOUT_MS) and the minimum display
        time, then shows `window` and closes. Returns straight away - the
        waiting happens once app.exec() is running."""
        self._window = window
        self._maps = list(maps)
        for view in self._maps:
            if view.load_state is None:
                view.ready.connect(self._check)
        QTimer.singleShot(MAP_TIMEOUT_MS, self._on_timeout)
        self._check()

    def _repaint_now(self):
        self.repaint()
        QApplication.processEvents()

    def _on_timeout(self):
        self._timed_out = True
        self._check()

    def _check(self, *_):
        if self._closing or self._window is None:
            return
        loaded = sum(1 for view in self._maps if view.load_state is not None)
        if loaded < len(self._maps) and not self._timed_out:
            self.step(f"Loading maps ({loaded}/{len(self._maps)})...",
                      0.9 + 0.1 * loaded / max(1, len(self._maps)))
            return

        self._closing = True
        self.step("Ready", 1.0)
        remaining = MIN_DISPLAY_MS - self._elapsed.elapsed()
        QTimer.singleShot(max(0, remaining), self._show_window)

    def _show_window(self):
        self._window.show()
        self.close()


if __name__ == "__main__":
    from ui.theme import load_fonts, build_stylesheet
    import time

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    splash = SplashScreen()
    splash.show()
    steps = ["Loading theme...", "Opening database...", "Building window...",
             "Loading Dashboard...", "Loading Live Map...", "Loading Logbook..."]
    for i, text in enumerate(steps):
        splash.step(text, i / len(steps) * 0.9)
        time.sleep(0.3)

    window = QLabel("Main window")
    window.resize(400, 200)
    splash.finish(window)
    sys.exit(app.exec())