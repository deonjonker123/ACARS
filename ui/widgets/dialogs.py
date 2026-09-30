"""
ui/widgets/dialogs.py

The app's dialogs, all in one style: no Windows title bar, a dark panel with
a thin square border, a large heading, and wide uppercase buttons. Drag
anywhere on the dialog (not a button) to move it.

Button styles:
    NEUTRAL    grey outline            Cancel, Close, OK
    DANGER     red outline + tint      Close Tailwind, Discard, Delete, Restore
    POSITIVE   green outline + tint    Resume, Save, View Debrief, Open Settings

Quick use (instead of QMessageBox):
    if confirm(self, "Delete Aircraft", "Delete N104TW? This cannot be undone.", "Delete"):
        ...
    inform(self, "Profile Not Saved", "KXYZ is not a known airport.")
    choice = choose(self, "Flight in Progress", "The app closed during a flight.",
                    [("resume", "Resume", POSITIVE), ("discard", "Discard", DANGER)],
                    default="resume", details="EZY123  EGKK -> LFPG ...")

Bigger dialogs (About, Add/Edit Aircraft) subclass ThemedDialog, put their
widgets in self.body, and add buttons with self.add_buttons().
"""

import os
import sys

from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_heading, font_label

NEUTRAL = "neutral"
DANGER = "danger"
POSITIVE = "positive"

POSITIVE_TEXT = "#4caf5f"
MESSAGE_WIDTH = 500

_BUTTON_NAMES = {NEUTRAL: "DialogNeutral", DANGER: "DialogDanger", POSITIVE: "DialogPositive"}

_STYLE = f"""
    QFrame#DialogFrame {{
        background-color: {PALETTE['bg']};
        border: 1px solid {PALETTE['border']};
        border-radius: 0px;
    }}
    QPushButton#DialogNeutral, QPushButton#DialogDanger, QPushButton#DialogPositive {{
        border-radius: 0px;
        padding: 13px 22px;
        font-size: 11px;
        outline: none;
    }}
    QPushButton#DialogNeutral {{
        background-color: transparent;
        color: {PALETTE['text_secondary']};
        border: 1px solid {PALETTE['border']};
    }}
    QPushButton#DialogNeutral:hover, QPushButton#DialogNeutral:focus {{
        color: {PALETTE['text_primary']};
        border-color: {PALETTE['border_light']};
    }}
    QPushButton#DialogDanger {{
        background-color: rgba(238, 38, 37, 0.07);
        color: {PALETTE['negative']};
        border: 1px solid rgba(238, 38, 37, 0.45);
    }}
    QPushButton#DialogDanger:hover, QPushButton#DialogDanger:focus {{
        background-color: rgba(238, 38, 37, 0.16);
        border-color: rgba(238, 38, 37, 0.8);
    }}
    QPushButton#DialogPositive {{
        background-color: rgba(45, 120, 61, 0.12);
        color: {POSITIVE_TEXT};
        border: 1px solid rgba(45, 120, 61, 0.8);
    }}
    QPushButton#DialogPositive:hover, QPushButton#DialogPositive:focus {{
        background-color: rgba(45, 120, 61, 0.24);
        border-color: {POSITIVE_TEXT};
    }}
    QPushButton:disabled {{
        background-color: transparent;
        color: {PALETTE['text_muted']};
        border-color: {PALETTE['border']};
    }}
"""


def dialog_button(text, style=NEUTRAL):
    """A wide uppercase dialog button in one of the three styles."""
    button = QPushButton(text.upper())
    button.setObjectName(_BUTTON_NAMES[style])
    button.setCursor(Qt.PointingHandCursor)
    f = font_label(10)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
    button.setFont(f)
    return button


class ThemedDialog(QDialog):
    """Frameless, square-bordered dialog. Content goes in self.body (below
    the title); buttons in a row at the bottom via add_buttons()."""

    def __init__(self, parent=None, title="", width=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setStyleSheet(_STYLE)
        if width:
            self.setFixedWidth(width)
        self._drag_offset = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("DialogFrame")
        outer.addWidget(frame)

        self._layout = QVBoxLayout(frame)
        self._layout.setContentsMargins(36, 30, 36, 30)
        self._layout.setSpacing(18)

        self.title_label = QLabel(title)
        self.title_label.setFont(font_heading(24))
        self.title_label.setStyleSheet(f"color: {PALETTE['text_primary']}; border: none;")
        self._layout.addWidget(self.title_label)

        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        self._layout.addLayout(self.body, stretch=1)

    def add_buttons(self, buttons, default=None):
        """buttons: [(key, text, style), ...] left to right, sharing the width
        equally. Returns {key: QPushButton}; connect them yourself."""
        row = QHBoxLayout()
        row.setSpacing(16)
        made = {}
        for key, text, style in buttons:
            button = dialog_button(text, style)
            button.setAutoDefault(False)
            if key == default:
                button.setDefault(True)
                button.setFocus()
            row.addWidget(button, stretch=1)
            made[key] = button
        self._layout.addSpacing(6)
        self._layout.addLayout(row)
        return made

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class MessageDialog(ThemedDialog):
    """A message with a row of buttons. After exec(), self.choice is the key
    of the button pressed, or `escape` if the dialog was closed with Esc."""

    def __init__(self, parent, title, text, buttons, default=None, escape=None, details=None):
        super().__init__(parent, title, width=MESSAGE_WIDTH)
        self.choice = escape
        self._escape = escape

        message = QLabel(text)
        message.setFont(font(11))
        message.setWordWrap(True)
        message.setStyleSheet(f"color: {PALETTE['text_secondary']}; border: none;")
        self.body.addWidget(message)

        if details:
            info = QLabel(details)
            info.setFont(font(10))
            info.setWordWrap(True)
            info.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info.setStyleSheet(f"color: {PALETTE['text_primary']}; border: none;")
            self.body.addWidget(info)

        for key, button in self.add_buttons(buttons, default).items():
            button.clicked.connect(lambda checked=False, k=key: self._pick(k))

    def _pick(self, key):
        self.choice = key
        self.accept()

    def reject(self):
        self.choice = self._escape
        super().reject()


def choose(parent, title, text, buttons, default=None, escape=None, details=None):
    """Shows a MessageDialog and returns the key of the button pressed (or
    `escape` for Esc). buttons: [(key, text, style), ...]."""
    dialog = MessageDialog(parent, title, text, buttons, default, escape, details)
    dialog.exec()
    return dialog.choice


def confirm(parent, title, text, confirm_text="OK", style=DANGER, cancel_text="Cancel", details=None):
    """Cancel / <confirm_text>. True only if the confirm button was pressed.
    A destructive confirm (the default style) leaves Cancel as the default."""
    default = "cancel" if style == DANGER else "confirm"
    return choose(parent, title, text,
                  [("cancel", cancel_text, NEUTRAL), ("confirm", confirm_text, style)],
                  default=default, escape="cancel", details=details) == "confirm"


def inform(parent, title, text, button_text="OK", details=None):
    """A message with a single button."""
    choose(parent, title, text, [("ok", button_text, NEUTRAL)], default="ok", escape="ok", details=details)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    print("confirm:", confirm(None, "Close Tailwind", "Are you sure you want to exit?", "Close Tailwind"))
    print("choose:", choose(None, "Flight in Progress", "The app closed during a flight.",
                            [("discard", "Discard", DANGER), ("resume", "Resume", POSITIVE)],
                            default="resume", details="EZY123   EGKK → LFPG   ·   A320 G-EZTW"))
    inform(None, "Profile Not Saved", "KXYZ is not a known airport.")