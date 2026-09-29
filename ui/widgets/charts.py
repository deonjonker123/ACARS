"""
ui/widgets/charts.py

Small hand-drawn chart widgets in the app's theme, for the debrief and
Pilot Profile pages - no charting library needed.

  BarChart(horizontal=False)   columns with a value axis (histograms,
                               buckets, flights per month)
  BarChart(horizontal=True)    ranked rows: label, bar, value at the tip
                               (top aircraft, top airports)
  LineChart()                  one or more series over x, with a hover
                               crosshair (flight profile, landing trend)

House rules (kept the same everywhere): one value axis per chart (never
two scales - altitude and speed are two charts), bars at most
BAR_MAX_PX thick with a rounded data-end and a square baseline, 2 px
lines, hairline gridlines, text in text colours (never the series
colour), a legend whenever there are two or more series, and a tooltip
on hover. A single series uses SERIES_COLORS[0]; bars can carry their
own colour (e.g. grade colours) when the colour means something.

Usage:
    chart = BarChart()
    chart.set_data([{"label": "0-2 h", "value": 14}, {"label": "2-4 h", "value": 6}],
                   value_format=lambda v: f"{v:,.0f} flights")

    chart = LineChart()
    chart.set_series([{"name": "Altitude", "points": [(0, 120), (5, 8000)]}],
                     x_format=lambda m: f"{m:.0f} min", y_format=lambda v: f"{v:,.0f} ft",
                     fill=True)
"""

import math
import os
import sys

from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFontMetrics

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ui.theme import PALETTE, font, font_label

SERIES_COLORS = ("#bf861a", "#4a8cc9")

BAR_MAX_PX = 24
BAR_RADIUS = 4
ROW_HEIGHT = 26
AXIS_FONT_SIZE = 8
EMPTY_TEXT = "No data yet"


def nice_ticks(low, high, count=4, integer=False):
    """Round axis ticks covering low..high (e.g. 0, 2,000, 4,000, 6,000).
    integer: whole-number steps only (for counts)."""
    if high is None or low is None:
        return [0, 1]
    if high == low:
        high = low + (abs(low) or 1)
    raw_step = (high - low) / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    if integer:
        step = max(1, math.ceil(step))
    start = math.floor(low / step) * step
    ticks = []
    value = start
    while value < high + step * 0.999:
        ticks.append(round(value, 10))
        value += step
    return ticks


def bar_path(rect, radius, direction):
    """A bar with its data-end rounded and its baseline end square.
    direction: "up" (column) or "right" (row)."""
    radius = min(radius, rect.width() / 2, rect.height() / 2)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    square = QPainterPath()
    if direction == "up":
        square.addRect(QRectF(rect.left(), rect.bottom() - radius, rect.width(), radius))
    else:
        square.addRect(QRectF(rect.left(), rect.top(), radius, rect.height()))
    return path.united(square)


class _Chart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._hover = None
        self._surface = QColor(PALETTE["bg_panel"])
        self._grid = QColor(PALETTE["border"])
        self._text = QColor(PALETTE["text_secondary"])
        self._text_strong = QColor(PALETTE["text_primary"])

    def _axis_font(self):
        return font_label(AXIS_FONT_SIZE)

    def _draw_empty(self, painter):
        painter.setFont(font(10))
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(self.rect(), Qt.AlignCenter, EMPTY_TEXT)

    def leaveEvent(self, event):
        self._hover = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)


class BarChart(_Chart):
    """Columns (default) or ranked horizontal rows. See the module docstring."""

    def __init__(self, horizontal=False, parent=None):
        super().__init__(parent)
        self.horizontal = horizontal
        self._items = []
        self._value_format = lambda v: f"{v:,.0f}"
        self._bars = []
        self.setMinimumHeight(160 if not horizontal else ROW_HEIGHT)

    def set_data(self, items, value_format=None):
        """items: [{"label", "value", "color" (optional), "tooltip" (optional)}]."""
        self._items = [dict(i) for i in items or []]
        if value_format is not None:
            self._value_format = value_format
        if self.horizontal:
            self.setMinimumHeight(max(ROW_HEIGHT, ROW_HEIGHT * len(self._items) + 4))
        self._hover = None
        self.update()

    def _color(self, item):
        return QColor(item.get("color") or SERIES_COLORS[0])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._bars = []
        if not self._items or all(not i.get("value") for i in self._items):
            self._draw_empty(painter)
            return
        if self.horizontal:
            self._paint_rows(painter)
        else:
            self._paint_columns(painter)

    def _paint_rows(self, painter):
        painter.setFont(self._axis_font())
        metrics = QFontMetrics(self._axis_font())
        label_w = min(self.width() * 0.4, max(metrics.horizontalAdvance(str(i["label"])) for i in self._items) + 12)
        value_w = max(metrics.horizontalAdvance(self._value_format(i["value"] or 0)) for i in self._items) + 10
        bar_left = label_w
        bar_room = max(10.0, self.width() - label_w - value_w)
        top_value = max(i["value"] or 0 for i in self._items) or 1
        thickness = min(BAR_MAX_PX, ROW_HEIGHT - 10)

        for index, item in enumerate(self._items):
            y = index * ROW_HEIGHT + 2
            row = QRectF(0, y, self.width(), ROW_HEIGHT)
            painter.setPen(self._text_strong if self._hover == index else self._text)
            label = metrics.elidedText(str(item["label"]), Qt.ElideRight, int(label_w - 12))
            painter.drawText(QRectF(0, y, label_w - 8, ROW_HEIGHT), Qt.AlignVCenter | Qt.AlignRight, label)

            length = bar_room * (item["value"] or 0) / top_value
            rect = QRectF(bar_left, y + (ROW_HEIGHT - thickness) / 2, max(length, 1.5), thickness)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color(item))
            painter.drawPath(bar_path(rect, BAR_RADIUS, "right"))

            painter.setPen(self._text_strong)
            painter.drawText(QRectF(rect.right() + 6, y, value_w, ROW_HEIGHT),
                             Qt.AlignVCenter | Qt.AlignLeft, self._value_format(item["value"] or 0))
            self._bars.append((row, index))

    def _paint_columns(self, painter):
        painter.setFont(self._axis_font())
        metrics = QFontMetrics(self._axis_font())
        values = [i["value"] or 0 for i in self._items]
        whole = all(float(v).is_integer() for v in values)
        ticks = nice_ticks(0, max(values), integer=whole)
        axis_w = max(metrics.horizontalAdvance(self._value_format(t)) for t in ticks) + 8
        label_h = metrics.height() + 6
        plot = QRectF(axis_w, 6, self.width() - axis_w - 4, self.height() - label_h - 10)
        top = ticks[-1] or 1

        for tick in ticks:
            y = plot.bottom() - plot.height() * tick / top
            painter.setPen(QPen(self._grid, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(self._text)
            painter.drawText(QRectF(0, y - 8, axis_w - 6, 16), Qt.AlignVCenter | Qt.AlignRight,
                             self._value_format(tick))

        slot = plot.width() / len(self._items)
        thickness = min(BAR_MAX_PX, max(2.0, slot - 2))
        label_every = max(1, math.ceil(len(self._items) * 40 / max(plot.width(), 1)))
        for index, item in enumerate(self._items):
            x_mid = plot.left() + slot * (index + 0.5)
            height = plot.height() * (item["value"] or 0) / top
            if height > 0:
                rect = QRectF(x_mid - thickness / 2, plot.bottom() - height, thickness, height)
                color = self._color(item)
                if self._hover is not None and self._hover != index:
                    color.setAlphaF(0.55)
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawPath(bar_path(rect, BAR_RADIUS, "up"))
            if index % label_every == 0:
                painter.setPen(self._text_strong if self._hover == index else self._text)
                label = metrics.elidedText(str(item["label"]), Qt.ElideRight, int(slot * label_every))
                painter.drawText(QRectF(x_mid - slot * label_every / 2, plot.bottom() + 4,
                                        slot * label_every, label_h), Qt.AlignHCenter | Qt.AlignTop, label)
            self._bars.append((QRectF(plot.left() + slot * index, plot.top(), slot, plot.height() + label_h),
                               index))

    def mouseMoveEvent(self, event):
        pos = event.position()
        hit = next((index for rect, index in self._bars if rect.contains(pos)), None)
        if hit != self._hover:
            self._hover = hit
            self.update()
        if hit is None:
            QToolTip.hideText()
            return
        item = self._items[hit]
        text = item.get("tooltip") or f"{item['label']}: {self._value_format(item['value'] or 0)}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


class LineChart(_Chart):
    """Series over x with a hover crosshair. See the module docstring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series = []
        self._x_format = lambda x: f"{x:,.0f}"
        self._y_format = lambda y: f"{y:,.0f}"
        self._x_tooltip = None
        self._fill = False
        self._y_zero = True
        self._plot = QRectF()
        self._x_range = (0, 1)
        self._y_ticks = [0, 1]
        self.setMinimumHeight(160)

    def set_series(self, series, x_format=None, y_format=None, fill=False, y_zero=True, x_tooltip=None):
        """series: [{"name", "points": [(x, y), ...], "color" (optional)}].
        fill: a light wash under a single series. y_zero: start the value
        axis at 0 (off for values that are all negative, e.g. landing rates).
        x_tooltip: how the hovered x reads in the tooltip, if not x_format."""
        self._series = []
        for index, s in enumerate(series or []):
            points = sorted((x, y) for x, y in s.get("points") or [] if x is not None and y is not None)
            if points:
                self._series.append({"name": s.get("name", ""), "points": points,
                                     "color": QColor(s.get("color") or SERIES_COLORS[index % len(SERIES_COLORS)])})
        if x_format is not None:
            self._x_format = x_format
        if y_format is not None:
            self._y_format = y_format
        self._x_tooltip = x_tooltip
        self._fill = fill and len(self._series) == 1
        self._y_zero = y_zero
        self._hover = None
        self.update()

    def _map(self, x, y):
        x0, x1 = self._x_range
        y0, y1 = self._y_ticks[0], self._y_ticks[-1]
        px = self._plot.left() + (x - x0) / ((x1 - x0) or 1) * self._plot.width()
        py = self._plot.bottom() - (y - y0) / ((y1 - y0) or 1) * self._plot.height()
        return QPointF(px, py)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._series:
            self._draw_empty(painter)
            return

        painter.setFont(self._axis_font())
        metrics = QFontMetrics(self._axis_font())
        xs = [x for s in self._series for x, _ in s["points"]]
        ys = [y for s in self._series for _, y in s["points"]]
        low, high = min(ys), max(ys)
        if self._y_zero:
            low, high = min(0, low), max(0, high)
        self._y_ticks = nice_ticks(low, high)
        self._x_range = (min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1)

        legend_h = metrics.height() + 8 if len(self._series) > 1 else 0
        axis_w = max(metrics.horizontalAdvance(self._y_format(t)) for t in self._y_ticks) + 8
        label_h = metrics.height() + 6
        self._plot = QRectF(axis_w, legend_h + 6, self.width() - axis_w - 12,
                            self.height() - legend_h - label_h - 10)

        if legend_h:
            x = axis_w
            for s in self._series:
                painter.setPen(QPen(s["color"], 2, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(x, legend_h / 2), QPointF(x + 14, legend_h / 2))
                painter.setPen(self._text)
                painter.drawText(QPointF(x + 20, legend_h / 2 + metrics.ascent() / 2 - 1), s["name"])
                x += 20 + metrics.horizontalAdvance(s["name"]) + 18

        for tick in self._y_ticks:
            y = self._map(self._x_range[0], tick).y()
            painter.setPen(QPen(self._grid, 1))
            painter.drawLine(QPointF(self._plot.left(), y), QPointF(self._plot.right(), y))
            painter.setPen(self._text)
            painter.drawText(QRectF(0, y - 8, axis_w - 6, 16), Qt.AlignVCenter | Qt.AlignRight,
                             self._y_format(tick))

        x_ticks = [t for t in nice_ticks(*self._x_range) if self._x_range[0] <= t <= self._x_range[1]]
        for tick in x_ticks:
            x = self._map(tick, self._y_ticks[0]).x()
            painter.setPen(self._text)
            painter.drawText(QRectF(x - 40, self._plot.bottom() + 4, 80, label_h),
                             Qt.AlignHCenter | Qt.AlignTop, self._x_format(tick))

        for s in self._series:
            path = QPainterPath()
            for i, (x, y) in enumerate(s["points"]):
                point = self._map(x, y)
                path.moveTo(point) if i == 0 else path.lineTo(point)
            if self._fill:
                area = QPainterPath(path)
                base = self._map(0, max(self._y_ticks[0], min(0, self._y_ticks[-1]))).y()
                area.lineTo(QPointF(self._map(s["points"][-1][0], 0).x(), base))
                area.lineTo(QPointF(self._map(s["points"][0][0], 0).x(), base))
                area.closeSubpath()
                wash = QColor(s["color"])
                wash.setAlphaF(0.12)
                painter.setPen(Qt.NoPen)
                painter.setBrush(wash)
                painter.drawPath(area)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(s["color"], 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        if self._hover is not None:
            x_px = self._map(self._hover, 0).x()
            painter.setPen(QPen(QColor(PALETTE["border_light"]), 1))
            painter.drawLine(QPointF(x_px, self._plot.top()), QPointF(x_px, self._plot.bottom()))
            for s in self._series:
                point = self._nearest(s, self._hover)
                if point is None:
                    continue
                centre = self._map(*point)
                painter.setPen(QPen(self._surface, 2))
                painter.setBrush(s["color"])
                painter.drawEllipse(centre, 5, 5)

    @staticmethod
    def _nearest(series, x):
        points = series["points"]
        if not points or x < points[0][0] or x > points[-1][0]:
            return None
        return min(points, key=lambda p: abs(p[0] - x))

    def mouseMoveEvent(self, event):
        if not self._series or self._plot.isEmpty():
            return
        pos = event.position()
        if not self._plot.adjusted(-4, -4, 4, 4).contains(pos):
            if self._hover is not None:
                self._hover = None
                QToolTip.hideText()
                self.update()
            return
        x0, x1 = self._x_range
        x = x0 + (pos.x() - self._plot.left()) / (self._plot.width() or 1) * (x1 - x0)
        first = self._nearest(self._series[0], min(max(x, x0), x1)) or self._series[0]["points"][0]
        self._hover = first[0]
        lines = [(self._x_tooltip or self._x_format)(self._hover)]
        for s in self._series:
            point = self._nearest(s, self._hover)
            if point is not None:
                label = f"{s['name']}: " if s["name"] else ""
                lines.append(f"{label}{self._y_format(point[1])}")
        QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
        self.update()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QVBoxLayout
    from ui.theme import load_fonts, build_stylesheet

    app = QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())

    window = QWidget()
    window.setStyleSheet(f"background-color: {PALETTE['bg_panel']};")
    layout = QVBoxLayout(window)

    columns = BarChart()
    columns.set_data([{"label": b, "value": v} for b, v in
                      (("0-2 h", 14), ("2-4 h", 6), ("4-8 h", 3), ("8-16 h", 1), ("16+ h", 0))],
                     value_format=lambda v: f"{v:,.0f}")
    layout.addWidget(columns)

    rows = BarChart(horizontal=True)
    rows.set_data([{"label": a, "value": h} for a, h in (("A320", 42.5), ("C172", 18.1), ("B738", 6.4))],
                  value_format=lambda v: f"{v:,.1f} h")
    layout.addWidget(rows)

    altitude = LineChart()
    altitude.set_series([{"name": "Altitude", "points": [(m, min(36000, m * 1500) if m < 60 else max(0, 36000 - (m - 60) * 1800))
                                                      for m in range(0, 81, 2)]}],
                        x_format=lambda m: f"{m:.0f} min", y_format=lambda v: f"{v:,.0f} ft", fill=True)
    layout.addWidget(altitude)

    speeds = LineChart()
    speeds.set_series([{"name": "IAS", "points": [(m, 250) for m in range(0, 81, 2)]},
                       {"name": "GS", "points": [(m, 250 + m) for m in range(0, 81, 2)]}],
                      x_format=lambda m: f"{m:.0f} min", y_format=lambda v: f"{v:,.0f} kt")
    layout.addWidget(speeds)

    window.resize(700, 820)
    window.show()
    sys.exit(app.exec())