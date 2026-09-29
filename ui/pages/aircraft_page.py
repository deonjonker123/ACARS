"""
ui/pages/aircraft_page.py

The Aircraft page: the active fleet shown as a grid of cards, one per
airframe (registration). Each card shows the image, aircraft name, reg,
categories, the airframe's own hours flown, and Edit / Delete buttons.

- Cards for aircraft the pilot isn't rated for yet are dimmed with
  "Unlocks at <rank>". They can still be edited or deleted.
- Add / Edit open the same dialog: name, registration, categories,
  unlock rank, image.
- Images are copied into assets/aircraft/ so the original file can be
  moved or deleted without breaking the card. image_path in the DB is
  stored relative to assets/ (e.g. "aircraft/3f2a...jpg"), the same way
  rank badges are.
- Delete asks for confirmation, then retires the aircraft (see
  core/db.py): it leaves the fleet, its logbook flights stay, pilot stats
  are untouched.
- A filter bar above the grid narrows the cards: search (name or reg),
  category, unlock rank, and "Unlocked only". Filters combine, and stay
  applied across refreshes (add/edit/delete, saved flights).

Usage:
    page = AircraftPage(db)
    page.refresh()   # call after anything changes airframe data (e.g. a saved flight)
"""

import os
import shutil
import sys
import uuid

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QDialog, QLineEdit, QCheckBox, QComboBox, QFileDialog,
    QMessageBox, QGraphicsOpacityEffect, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label, WEIGHT_MEDIUM
from core.db import VALID_CATEGORIES
from core.pilot import PilotProgress, load_fleet_data
from core.paths import USER_DATA_DIR, AIRCRAFT_IMAGE_DIR

_IMAGE_BASE_DIR = USER_DATA_DIR
_AIRCRAFT_IMAGE_DIR = AIRCRAFT_IMAGE_DIR

CARD_WIDTH = 260
IMAGE_HEIGHT = 146
GRID_SPACING = 16

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp)"

CATEGORY_LABELS = {
    "prop": "Prop",
    "airliner": "Airliner",
    "bizjet": "Bizjet",
    "cargo": "Cargo",
}

_INPUT_STYLE = f"""
    QLineEdit, QComboBox {{
        background-color: {PALETTE['bg_input']};
        color: {PALETTE['text_primary']};
        border: 1px solid {PALETTE['border']};
        border-radius: 4px;
        padding: 6px 8px;
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {PALETTE['accent_dim']}; }}
    QComboBox QAbstractItemView {{
        background-color: {PALETTE['bg_input']};
        color: {PALETTE['text_primary']};
        selection-background-color: {PALETTE['accent_bg']};
        selection-color: {PALETTE['accent']};
    }}
    QCheckBox {{ color: {PALETTE['text_primary']}; spacing: 6px; }}
"""

def _format_hours(hours):
    total_minutes = round((hours or 0) * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m"


def _format_categories(categories):
    if not categories:
        return "No category"
    return "  ·  ".join(CATEGORY_LABELS.get(c, c.title()) for c in categories)


def _absolute_image_path(image_path):
    """image_path is stored relative to assets/ - returns the full path,
    or None if there's no image or the file has gone missing."""
    if not image_path:
        return None
    full = os.path.join(_IMAGE_BASE_DIR, image_path)
    return full if os.path.exists(full) else None


def _cropped_pixmap(path, width, height):
    """Scales an image to fill width x height, cropping the overflow from
    the centre, so every card image is the same size regardless of the
    source image's aspect ratio. Returns None if the file can't be loaded."""
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    scaled = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (scaled.width() - width) // 2
    y = (scaled.height() - height) // 2
    return scaled.copy(x, y, width, height)


def _image_label(image_path, width, height):
    """A fixed-size label showing the aircraft image, or a 'NO IMAGE'
    placeholder if there isn't one."""
    label = QLabel()
    label.setFixedSize(width, height)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(f"background-color: {PALETTE['bg_input']}; border-radius: 3px;")

    full_path = _absolute_image_path(image_path)
    pixmap = _cropped_pixmap(full_path, width, height) if full_path else None
    if pixmap is not None:
        label.setPixmap(pixmap)
    else:
        label.setText("NO IMAGE")
        label.setFont(font_label(9))
        label.setStyleSheet(
            f"background-color: {PALETTE['bg_input']}; border-radius: 3px; "
            f"color: {PALETTE['text_muted']};"
        )
    return label


def _copy_image_into_assets(source_path):
    """Copies a picked image into assets/aircraft/ under a unique name.
    Returns the path relative to assets/ for storing in the DB."""
    os.makedirs(_AIRCRAFT_IMAGE_DIR, exist_ok=True)
    ext = os.path.splitext(source_path)[1].lower() or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    shutil.copy2(source_path, os.path.join(_AIRCRAFT_IMAGE_DIR, filename))
    return f"aircraft/{filename}"


def _remove_image_file(image_path):
    full = _absolute_image_path(image_path)
    if full:
        try:
            os.remove(full)
        except OSError as e:
            print(f"[aircraft_page] Could not remove old image {full}: {e}")

class AircraftDialog(QDialog):
    """Add (aircraft=None) or Edit (aircraft=dict from db) an airframe.
    Writes to the DB itself on Save, and only closes if that succeeded -
    validation errors (duplicate reg, no category, ...) are shown and the
    dialog stays open with everything still filled in."""

    def __init__(self, db, aircraft=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.aircraft = aircraft
        self.is_edit = aircraft is not None
        self._original_image = aircraft.get("image_path") if aircraft else None
        self._picked_image_source = None
        self._image_removed = False

        self.setWindowTitle(
            f"Edit Aircraft - {aircraft['registration']}" if self.is_edit else "Add Aircraft"
        )
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(
            f"QDialog {{ background-color: {PALETTE['bg_panel']}; }}" + _INPUT_STYLE
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(self._field_label("AIRCRAFT"))
        self.name_input = QLineEdit(aircraft["designation"] if aircraft else "")
        self.name_input.setPlaceholderText("e.g. C172")
        layout.addWidget(self.name_input)

        layout.addWidget(self._field_label("REGISTRATION"))
        reg_row = QHBoxLayout()
        self.reg_input = QLineEdit(aircraft["registration"] if aircraft else db.generate_registration())
        self.reg_input.setPlaceholderText("e.g. N482TW")
        self.reg_input.textEdited.connect(self._force_uppercase)
        reg_row.addWidget(self.reg_input, stretch=1)
        regen_btn = QPushButton("Generate")
        regen_btn.setObjectName("TableActionButton")
        regen_btn.setCursor(Qt.PointingHandCursor)
        regen_btn.setToolTip("Generate a new unused Tailwind registration")
        regen_btn.clicked.connect(lambda checked=False: self.reg_input.setText(self.db.generate_registration()))
        reg_row.addWidget(regen_btn)
        layout.addLayout(reg_row)
        if self.is_edit:
            hint = QLabel("Changing the registration moves this aircraft's logbook flights with it.")
            hint.setFont(font(8))
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {PALETTE['text_muted']};")
            layout.addWidget(hint)

        layout.addWidget(self._field_label("CATEGORY"))
        cat_row = QHBoxLayout()
        self.category_boxes = {}
        current_categories = set(aircraft["categories"]) if aircraft else set()
        for cat in VALID_CATEGORIES:
            box = QCheckBox(CATEGORY_LABELS.get(cat, cat.title()))
            box.setChecked(cat in current_categories)
            box.setCursor(Qt.PointingHandCursor)
            self.category_boxes[cat] = box
            cat_row.addWidget(box)
        cat_row.addStretch()
        layout.addLayout(cat_row)

        layout.addWidget(self._field_label("UNLOCK RANK"))
        self.rank_combo = QComboBox()
        ranks, _ = load_fleet_data()
        for r in ranks:
            self.rank_combo.addItem(f"{r['name']}  ({r['min_hours']}+ hrs)", r["id"])
        current_rank = (aircraft.get("unlock_rank") if aircraft else None) or "student_pilot"
        index = self.rank_combo.findData(current_rank)
        self.rank_combo.setCurrentIndex(max(index, 0))
        layout.addWidget(self.rank_combo)

        layout.addWidget(self._field_label("IMAGE"))
        self.preview_width = 372
        self.preview_height = 209
        self.image_preview = QLabel()
        layout.addWidget(self.image_preview, alignment=Qt.AlignLeft)
        img_row = QHBoxLayout()
        choose_btn = QPushButton("Choose Image...")
        choose_btn.setCursor(Qt.PointingHandCursor)
        choose_btn.clicked.connect(self._choose_image)
        img_row.addWidget(choose_btn)
        self.remove_img_btn = QPushButton("Remove Image")
        self.remove_img_btn.setCursor(Qt.PointingHandCursor)
        self.remove_img_btn.clicked.connect(self._remove_image)
        img_row.addWidget(self.remove_img_btn)
        img_row.addStretch()
        layout.addLayout(img_row)
        self._update_preview()

        layout.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save" if self.is_edit else "Add Aircraft")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _field_label(self, text):
        label = QLabel(text)
        label.setFont(font_label(9))
        label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        return label

    def _force_uppercase(self, text):
        cursor = self.reg_input.cursorPosition()
        self.reg_input.setText(text.upper())
        self.reg_input.setCursorPosition(cursor)

    def _current_preview_path(self):
        if self._picked_image_source:
            return self._picked_image_source
        if self._image_removed:
            return None
        return _absolute_image_path(self._original_image)

    def _update_preview(self):
        path = self._current_preview_path()
        pixmap = _cropped_pixmap(path, self.preview_width, self.preview_height) if path else None
        self.image_preview.setFixedSize(self.preview_width, self.preview_height)
        self.image_preview.setAlignment(Qt.AlignCenter)
        if pixmap is not None:
            self.image_preview.setPixmap(pixmap)
            self.image_preview.setStyleSheet("border-radius: 3px;")
        else:
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText("NO IMAGE")
            self.image_preview.setFont(font_label(9))
            self.image_preview.setStyleSheet(
                f"background-color: {PALETTE['bg_input']}; border-radius: 3px; "
                f"color: {PALETTE['text_muted']};"
            )
        self.remove_img_btn.setEnabled(path is not None)

    def _choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Aircraft Image", "", IMAGE_FILTER)
        if not path:
            return
        if QPixmap(path).isNull():
            QMessageBox.warning(self, "Invalid Image", "That file couldn't be loaded as an image.")
            return
        self._picked_image_source = path
        self._image_removed = False
        self._update_preview()

    def _remove_image(self):
        self._picked_image_source = None
        self._image_removed = True
        self._update_preview()

    def _save(self):
        categories = [c for c, box in self.category_boxes.items() if box.isChecked()]
        unlock_rank = self.rank_combo.currentData()
        name = self.name_input.text()
        registration = self.reg_input.text()

        new_copy = None
        if self._picked_image_source:
            try:
                new_copy = _copy_image_into_assets(self._picked_image_source)
            except OSError as e:
                QMessageBox.warning(self, "Image Not Saved", f"Couldn't copy the image: {e}")
                return
            image_path = new_copy
        elif self._image_removed:
            image_path = None
        else:
            image_path = self._original_image

        try:
            if self.is_edit:
                self.db.update_aircraft(
                    self.aircraft["registration"], registration, name,
                    categories, unlock_rank, image_path=image_path,
                )
            else:
                self.db.add_aircraft(
                    registration, name, categories, unlock_rank, image_path=image_path,
                )
        except ValueError as e:
            if new_copy:
                _remove_image_file(new_copy)
            QMessageBox.warning(self, "Aircraft Not Saved", str(e))
            return

        if self._original_image and image_path != self._original_image:
            _remove_image_file(self._original_image)

        self.accept()

class AircraftCard(QWidget):
    def __init__(self, aircraft, locked_rank_name, on_edit, on_delete):
        super().__init__()
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(CARD_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 12)
        layout.setSpacing(4)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)

        content_layout.addWidget(_image_label(aircraft.get("image_path"), CARD_WIDTH - 20, IMAGE_HEIGHT))
        content_layout.addSpacing(6)

        name = QLabel(aircraft["designation"])
        name.setFont(font(15, WEIGHT_MEDIUM))
        name.setStyleSheet(f"color: {PALETTE['text_primary']};")
        content_layout.addWidget(name)

        reg = QLabel(aircraft["registration"])
        reg.setFont(font_label(10))
        reg.setStyleSheet(f"color: {PALETTE['accent']};")
        content_layout.addWidget(reg)

        category = QLabel(_format_categories(aircraft["categories"]))
        category.setFont(font(9))
        category.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        content_layout.addWidget(category)

        hours = QLabel(f"{_format_hours(aircraft['hours_flown'])} flown")
        hours.setFont(font(9))
        hours.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        content_layout.addWidget(hours)

        if locked_rank_name:
            effect = QGraphicsOpacityEffect(content)
            effect.setOpacity(0.4)
            content.setGraphicsEffect(effect)

        layout.addWidget(content)

        lock_label = QLabel(f"Unlocks at {locked_rank_name}" if locked_rank_name else "")
        lock_label.setFont(font_label(8))
        lock_label.setStyleSheet(f"color: {PALETTE['warning']};")
        lock_label.setVisible(bool(locked_rank_name))
        layout.addWidget(lock_label)

        layout.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("TableActionButton")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(on_edit)
        btn_row.addWidget(edit_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("TableActionButton")
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.clicked.connect(on_delete)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

class AircraftPage(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._cards = []
        self._visible_cards = []
        self._columns = 0
        self._unlocked_total = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        self.summary_label = QLabel("")
        self.summary_label.setFont(font_label(10))
        self.summary_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        top_row.addWidget(self.summary_label)
        top_row.addStretch()
        add_btn = QPushButton("Add Aircraft")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_aircraft)
        top_row.addWidget(add_btn)
        layout.addLayout(top_row)

        layout.addWidget(self._build_filter_bar())

        self.empty_label = QLabel("No aircraft match your filters.")
        self.empty_label.setFont(font(11))
        self.empty_label.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setObjectName("AircraftScroll")
        self.scroll.viewport().setObjectName("AircraftViewport")
        self.scroll.setStyleSheet(f"""
            QScrollArea#AircraftScroll,
            QWidget#AircraftViewport,
            QWidget#AircraftGrid {{
                background-color: {PALETTE['bg']};
                border: none;
            }}
        """)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("AircraftGrid")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(GRID_SPACING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        layout.addWidget(self.scroll)

        self.refresh()

    def _build_filter_bar(self):
        bar = QWidget()
        bar.setStyleSheet(_INPUT_STYLE)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search name or reg...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(220)
        self.search_input.textChanged.connect(self._apply_filters)
        row.addWidget(self.search_input, stretch=1)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories", None)
        for cat in VALID_CATEGORIES:
            self.category_filter.addItem(CATEGORY_LABELS.get(cat, cat.title()), cat)
        self.category_filter.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.category_filter)

        self.rank_filter = QComboBox()
        self.rank_filter.addItem("All ranks", None)
        ranks, _ = load_fleet_data()
        for r in ranks:
            self.rank_filter.addItem(r["name"], r["id"])
        self.rank_filter.currentIndexChanged.connect(self._apply_filters)
        row.addWidget(self.rank_filter)

        self.unlocked_only = QCheckBox("Unlocked only")
        self.unlocked_only.setCursor(Qt.PointingHandCursor)
        self.unlocked_only.toggled.connect(self._apply_filters)
        row.addWidget(self.unlocked_only)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("TableActionButton")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_filters)
        row.addWidget(clear_btn)

        return bar

    def _filters_active(self):
        return bool(
            self.search_input.text().strip()
            or self.category_filter.currentData()
            or self.rank_filter.currentData()
            or self.unlocked_only.isChecked()
        )

    def _matches(self, aircraft, locked):
        text = self.search_input.text().strip().lower()
        if text and text not in aircraft["designation"].lower() \
                and text not in aircraft["registration"].lower():
            return False

        category = self.category_filter.currentData()
        if category and category not in aircraft["categories"]:
            return False

        rank_id = self.rank_filter.currentData()
        if rank_id and (aircraft.get("unlock_rank") or "student_pilot") != rank_id:
            return False

        if self.unlocked_only.isChecked() and locked:
            return False

        return True

    def _apply_filters(self, *_):
        visible = [card for card, a, locked in self._cards if self._matches(a, locked)]
        for card, _a, _locked in self._cards:
            card.setVisible(card in visible)
        self._visible_cards = visible

        total = len(self._cards)
        if self._filters_active():
            shown_unlocked = sum(1 for card, _a, locked in self._cards if card in visible and not locked)
            self.summary_label.setText(
                f"{len(visible)} OF {total} AIRCRAFT  ·  {shown_unlocked} UNLOCKED"
            )
        else:
            self.summary_label.setText(f"{total} AIRCRAFT  ·  {self._unlocked_total} UNLOCKED")

        self.empty_label.setVisible(total > 0 and not visible)
        self._columns = 0
        self._layout_cards()

    def _clear_filters(self):
        for w in (self.search_input, self.category_filter, self.rank_filter, self.unlocked_only):
            w.blockSignals(True)
        self.search_input.clear()
        self.category_filter.setCurrentIndex(0)
        self.rank_filter.setCurrentIndex(0)
        self.unlocked_only.setChecked(False)
        for w in (self.search_input, self.category_filter, self.rank_filter, self.unlocked_only):
            w.blockSignals(False)
        self._apply_filters()

    def refresh(self):
        """Reloads every card from the database. Current filters stay applied."""
        for card, _a, _locked in self._cards:
            self.grid.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._visible_cards = []

        pilot = self.db.get_pilot()
        progress = PilotProgress(pilot["total_hours_flown"] if pilot else 0)
        rank_by_id = {r["id"]: r for r in progress.ranks}

        def required_rank(a):
            return rank_by_id.get(a.get("unlock_rank") or "student_pilot", progress.ranks[0])

        fleet = sorted(
            self.db.list_aircraft(),
            key=lambda a: (required_rank(a)["order"], a["designation"].lower(), a["registration"]),
        )

        unlocked_count = 0
        for a in fleet:
            rank = required_rank(a)
            locked = progress.total_hours < rank["min_hours"]
            if not locked:
                unlocked_count += 1
            card = AircraftCard(
                a,
                locked_rank_name=rank["name"] if locked else None,
                on_edit=lambda checked=False, reg=a["registration"]: self._edit_aircraft(reg),
                on_delete=lambda checked=False, ac=a: self._delete_aircraft(ac),
            )
            card.setParent(self.grid_host)
            self._cards.append((card, a, locked))

        self._unlocked_total = unlocked_count
        self._apply_filters()

    def _layout_cards(self):
        available = self.scroll.viewport().width()
        columns = max(1, (available + GRID_SPACING) // (CARD_WIDTH + GRID_SPACING))
        if columns == self._columns:
            return
        self._columns = columns
        for card, _a, _locked in self._cards:
            self.grid.removeWidget(card)
        for i, card in enumerate(self._visible_cards):
            self.grid.addWidget(card, i // columns, i % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_cards()

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_cards()

    def _add_aircraft(self):
        if AircraftDialog(self.db, parent=self).exec() == QDialog.Accepted:
            self.refresh()

    def _edit_aircraft(self, registration):
        aircraft = self.db.get_aircraft(registration)
        if aircraft is None:
            self.refresh()
            return
        if AircraftDialog(self.db, aircraft=aircraft, parent=self).exec() == QDialog.Accepted:
            self.refresh()

    def _delete_aircraft(self, aircraft):
        answer = QMessageBox.question(
            self,
            "Delete Aircraft",
            f"Delete {aircraft['registration']} ({aircraft['designation']})? "
            f"This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.db.delete_aircraft(aircraft["registration"])
        self.refresh()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet
    from core.db import FlightDatabase

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    db = FlightDatabase()
    db.init_db()

    page = AircraftPage(db)
    page.resize(1200, 800)
    page.show()
    sys.exit(app.exec())