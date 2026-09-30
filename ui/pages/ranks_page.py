"""
ui/pages/ranks_page.py

The Ranks page - purely informational: every rank from fleet_and_ranks.json,
the hours it takes, what it unlocks, and where the pilot currently stands.

- One card per rank, lowest first: badge, name, hours required / hour band,
  and the aircraft it unlocks (from the LIVE fleet on the Aircraft page,
  counted per airframe, so added/edited/deleted aircraft are reflected).
- Reached ranks get a green check, the current rank is highlighted in gold
  with a progress bar to the next rank, ranks not yet reached are dimmed.
- Ranks that unlock nothing in the current fleet are marked "Prestige rank".
- Rebuilt every time the page is shown, so it's current after a saved flight.

Usage:
    page = RanksPage(db)
"""

import os
import sys

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QProgressBar, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label, WEIGHT_MEDIUM
from core.pilot import PilotProgress

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BADGE_DIR = os.path.join(_PROJECT_ROOT, "assets", "badges")

BADGE_WIDTH = 96
BADGE_HEIGHT = 34
DIMMED_OPACITY = 0.45


def _hours(value):
    """21 -> '21', 250.9 -> '250.9', 3000 -> '3,000'."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def _badge_label(rank_id):
    label = QLabel()
    label.setFixedSize(BADGE_WIDTH, BADGE_HEIGHT)
    label.setAlignment(Qt.AlignCenter)
    pixmap = QPixmap(os.path.join(_BADGE_DIR, f"{rank_id}.png"))
    if not pixmap.isNull():
        label.setPixmap(pixmap.scaled(BADGE_WIDTH, BADGE_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return label


class RanksPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        self.summary_label = QLabel("")
        self.summary_label.setFont(font_label(10))
        self.summary_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(self.summary_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setObjectName("RanksScroll")
        self.scroll.viewport().setObjectName("RanksViewport")
        self.scroll.setStyleSheet(f"""
            QScrollArea#RanksScroll, QWidget#RanksViewport, QWidget#RanksList {{
                background-color: {PALETTE['bg']};
                border: none;
            }}
        """)
        layout.addWidget(self.scroll)

        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        pilot = self.db.get_pilot() or {}
        progress = PilotProgress(pilot.get("total_hours_flown") or 0)
        ranks = progress.ranks
        rank_ids = {r["id"] for r in ranks}

        fleet = self.db.list_aircraft()
        by_rank = {r["id"]: [] for r in ranks}
        for a in fleet:
            rank_id = a.get("unlock_rank") if a.get("unlock_rank") in rank_ids else "student_pilot"
            by_rank[rank_id].append(a)

        unlocked = sum(
            len(by_rank[r["id"]]) for r in ranks if progress.total_hours >= r["min_hours"]
        )
        self.summary_label.setText(
            f"{progress.current_rank['name'].upper()}  ·  {_hours(round(progress.total_hours, 1))} HRS  ·  "
            f"{unlocked} OF {len(fleet)} AIRCRAFT UNLOCKED"
        )

        container = QWidget()
        container.setObjectName("RanksList")
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        for i, rank in enumerate(ranks):
            next_rank = ranks[i + 1] if i + 1 < len(ranks) else None
            column.addWidget(self._rank_card(rank, next_rank, by_rank[rank["id"]], progress))
        column.addStretch()

        old = self.scroll.takeWidget()
        self.scroll.setWidget(container)
        if old is not None:
            old.deleteLater()

    def _rank_card(self, rank, next_rank, aircraft, progress):
        reached = progress.total_hours >= rank["min_hours"]
        current = rank["id"] == progress.current_rank["id"]

        card = QWidget()
        card.setObjectName("Card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        if current:
            card.setStyleSheet(f"""
                QWidget#Card {{
                    background-color: {PALETTE['accent_bg']};
                    border: 1px solid {PALETTE['accent']};
                }}
            """)
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 14, 20, 14)
        row.setSpacing(24)

        content = QWidget()
        content_row = QHBoxLayout(content)
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(24)

        content_row.addWidget(_badge_label(rank["id"]))

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name = QLabel(rank["name"])
        name.setFont(font(14, WEIGHT_MEDIUM))
        name.setStyleSheet(f"color: {PALETTE['accent'] if current else PALETTE['text_primary']};")
        name_col.addWidget(name)

        if next_rank is not None:
            band = f"{_hours(rank['min_hours'])} – {_hours(next_rank['min_hours'] - 0.1)} hrs"
        else:
            band = f"{_hours(rank['min_hours'])}+ hrs"
        hours = QLabel(f"Requires {_hours(rank['min_hours'])} hrs   ·   {band}")
        hours.setFont(font(9))
        hours.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        name_col.addWidget(hours)
        name_widget = QWidget()
        name_widget.setLayout(name_col)
        name_widget.setFixedWidth(260)
        content_row.addWidget(name_widget)

        unlocks_col = QVBoxLayout()
        unlocks_col.setSpacing(2)
        unlocks_col.addWidget(self._small_caption("UNLOCKS"))
        if aircraft:
            names = sorted({a["designation"] for a in aircraft}, key=str.lower)
            count = len(aircraft)
            text = f"{count} aircraft  ·  {', '.join(names)}"
            color = PALETTE["text_primary"]
        else:
            text = "Prestige rank - no new aircraft"
            color = PALETTE["text_muted"]
        unlocks = QLabel(text)
        unlocks.setFont(font(10))
        unlocks.setWordWrap(True)
        unlocks.setStyleSheet(f"color: {color};")
        unlocks_col.addWidget(unlocks)
        unlocks_widget = QWidget()
        unlocks_widget.setLayout(unlocks_col)
        content_row.addWidget(unlocks_widget, stretch=1)

        if not reached:
            effect = QGraphicsOpacityEffect(content)
            effect.setOpacity(DIMMED_OPACITY)
            content.setGraphicsEffect(effect)
        row.addWidget(content, stretch=1)

        row.addWidget(self._status_column(rank, next_rank, progress, reached, current))
        return card

    def _status_column(self, rank, next_rank, progress, reached, current):
        widget = QWidget()
        widget.setFixedWidth(200)
        col = QVBoxLayout(widget)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addStretch()

        if current:
            label = QLabel("CURRENT RANK")
            label.setFont(font_label(9))
            label.setStyleSheet(f"color: {PALETTE['accent']};")
            col.addWidget(label, alignment=Qt.AlignRight)

            if next_rank is not None:
                span = next_rank["min_hours"] - rank["min_hours"]
                fraction = (progress.total_hours - rank["min_hours"]) / span if span > 0 else 1.0
                bar = QProgressBar()
                bar.setObjectName("RankProgress")
                bar.setTextVisible(False)
                bar.setFixedWidth(180)
                bar.setValue(int(max(0.0, min(1.0, fraction)) * 100))
                col.addWidget(bar, alignment=Qt.AlignRight)
                to_go = QLabel(f"{_hours(progress.hours_to_next_rank)} hrs to {next_rank['name']}")
            else:
                to_go = QLabel("Top rank reached")
            to_go.setFont(font(8))
            to_go.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            col.addWidget(to_go, alignment=Qt.AlignRight)
        elif reached:
            label = QLabel("✓  ACHIEVED")
            label.setFont(font_label(9))
            label.setStyleSheet(f"color: {PALETTE['positive']};")
            col.addWidget(label, alignment=Qt.AlignRight)
        else:
            remaining = rank["min_hours"] - progress.total_hours
            label = QLabel(f"{_hours(round(remaining, 1))} hrs to go")
            label.setFont(font_label(9))
            label.setStyleSheet(f"color: {PALETTE['text_muted']};")
            col.addWidget(label, alignment=Qt.AlignRight)

        col.addStretch()
        return widget

    def _small_caption(self, text):
        label = QLabel(text)
        label.setFont(font_label(8))
        label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        return label


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet
    from core.db import FlightDatabase

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    db = FlightDatabase()
    db.init_db()

    page = RanksPage(db)
    page.resize(1200, 800)
    page.show()
    sys.exit(app.exec())