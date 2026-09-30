"""
ui/widgets/unit_converters.py

The Converters tab on the Flight Plan page: weight (kg / lb - fuel too),
pressure (hPa / inHg) and temperature (°C / °F). Each card has one field per
unit; typing in either fills in the other.

The conversion maths is plain functions (convert()) so it can be checked
without a window.

Usage:
    tabs.addTab(UnitConverters(), "Converters")
"""

import os
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QFrame
from PySide6.QtCore import Qt

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label

LB_PER_KG = 2.20462262185
HPA_PER_INHG = 33.8639

CONVERTERS = [
    ("WEIGHT", "Payload, fuel, any weight", [
        ("kg", lambda v: v, lambda v: v, 1),
        ("lb", lambda v: v / LB_PER_KG, lambda v: v * LB_PER_KG, 1),
    ]),
    ("PRESSURE", "QNH / altimeter setting", [
        ("hPa", lambda v: v, lambda v: v, 1),
        ("inHg", lambda v: v * HPA_PER_INHG, lambda v: v / HPA_PER_INHG, 2),
    ]),
    ("TEMPERATURE", "OAT, SAT, ISA", [
        ("°C", lambda v: v, lambda v: v, 1),
        ("°F", lambda v: (v - 32) * 5 / 9, lambda v: v * 9 / 5 + 32, 1),
    ]),
]

_STYLE = f"""
    QFrame#ConverterCard {{
        background-color: {PALETTE['bg']};
        border: 1px solid {PALETTE['border']};
    }}
    QLineEdit:focus {{ border-color: {PALETTE['accent_dim']}; }}
"""


def parse_number(text):
    """"1,013.2" / "29.92" / "-5" -> float; None if it isn't a number.
    Commas are taken as thousands separators."""
    try:
        return float(text.strip().replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def format_number(value, decimals):
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def convert(value, from_unit, to_unit):
    """convert(value, from, to) with units from CONVERTERS, e.g.
    convert(29.92, "inHg", "hPa") -> 1013.21..."""
    for _, _, units in CONVERTERS:
        by_label = {u[0]: u for u in units}
        if from_unit in by_label and to_unit in by_label:
            return by_label[to_unit][2](by_label[from_unit][1](value))
    raise ValueError(f"Can't convert {from_unit} to {to_unit}")


class _ConverterCard(QFrame):
    def __init__(self, title, hint, units):
        super().__init__()
        self.setObjectName("ConverterCard")
        self._units = units
        self._fields = []
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setFont(font_label(9))
        heading.setStyleSheet(f"color: {PALETTE['text_secondary']}; border: none;")
        layout.addWidget(heading)
        sub = QLabel(hint)
        sub.setFont(font(9))
        sub.setStyleSheet(f"color: {PALETTE['text_muted']}; border: none;")
        layout.addWidget(sub)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for row, (label, _, _, _) in enumerate(units):
            field = QLineEdit()
            field.setPlaceholderText("0")
            field.setAlignment(Qt.AlignRight)
            field.textEdited.connect(lambda text, index=row: self._edited(index, text))
            grid.addWidget(field, row, 0)
            unit = QLabel(label)
            unit.setFont(font_label(10))
            unit.setMinimumWidth(36)
            unit.setStyleSheet(f"color: {PALETTE['accent']}; border: none;")
            grid.addWidget(unit, row, 1)
            self._fields.append(field)
        layout.addLayout(grid)
        layout.addStretch()

    def _edited(self, index, text):
        if self._updating:
            return
        value = parse_number(text)
        base = self._units[index][1](value) if value is not None else None
        self._updating = True
        try:
            for other, (_, _, from_base, decimals) in enumerate(self._units):
                if other == index:
                    continue
                self._fields[other].setText("" if base is None else format_number(from_base(base), decimals))
        finally:
            self._updating = False


class UnitConverters(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        row = QHBoxLayout()
        row.setSpacing(16)
        for title, hint, units in CONVERTERS:
            card = _ConverterCard(title, hint, units)
            card.setFixedWidth(260)
            row.addWidget(card)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    from ui.theme import load_fonts, build_stylesheet

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    widget = UnitConverters()
    widget.resize(900, 300)
    widget.show()
    sys.exit(app.exec())