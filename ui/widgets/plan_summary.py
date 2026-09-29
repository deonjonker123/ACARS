"""
ui/widgets/plan_summary.py

The flight plan summary block, shared by the Home page (Zone 1) and the
Flight Plan page so both always show a plan the same way:

    TW001   KLAX → KSMO   ALT KSNA                       [DISPATCHED · C172 N104TW]
    LOS ANGELES INTL → SANTA MONICA MUN   (alternate: JOHN WAYNE-ORANGE CO)
    OFP AIRCRAFT   CRUISE     DISTANCE   EST. BLOCK   PAX   CARGO
    C172           5,000 ft   44 nm      0h 54m       3     44 kg
    DCT LAX LAX046 ELMOO V186 DARTS DCT

The detail figures sit in one row by default. Pass columns=3 to wrap them
into rows of three instead (the Dashboard, where the summary sits in a
narrow column):
    OFP AIRCRAFT   CRUISE     DISTANCE
    C172           5,000 ft   44 nm
    EST. BLOCK     PAX        CARGO
    0h 54m         3          44 kg

Usage:
    summary = PlanSummary(show_badge=True)
    summary = PlanSummary(columns=3)
    summary.set_plan(plan, dispatch=controller.dispatch_info)   # plan = core.simbrief dict
    summary.set_plan(None)                                      # clears it
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel

from ui.theme import PALETTE, font, font_label, WEIGHT_MEDIUM


def _fmt(value, unit="", decimals=1):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{decimals}f}{unit}"
    return f"{value}{unit}"


def fmt_hm(hours):
    """0.893 -> '0h 54m'. Shared with the pages that use this widget."""
    if hours is None:
        return "—"
    total_minutes = round(hours * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m"


DETAIL_FIELDS = [
    ("aircraft_type", "OFP AIRCRAFT"),
    ("cruise", "CRUISE"),
    ("distance", "DISTANCE"),
    ("est_block", "EST. BLOCK"),
    ("pax", "PAX"),
    ("cargo", "CARGO"),
]


class PlanSummary(QWidget):
    def __init__(self, show_badge=False, columns=None, parent=None):
        """show_badge: show the DISPATCHED · <aircraft> badge on the title
        line (the Home page has its own dispatch status, so it leaves this off).
        columns: detail figures per row - None puts them all in one row."""
        super().__init__(parent)
        self.show_badge = show_badge

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        self.title = QLabel("")
        self.title.setFont(font(16, WEIGHT_MEDIUM))
        self.title.setStyleSheet(f"color: {PALETTE['text_primary']};")
        title_row.addWidget(self.title)
        title_row.addStretch()
        self.badge = QLabel("")
        self.badge.setObjectName("Badge")
        self.badge.setFont(font_label(9))
        self.badge.setVisible(False)
        title_row.addWidget(self.badge)
        layout.addLayout(title_row)

        self.airports = QLabel("")
        self.airports.setFont(font(9))
        self.airports.setWordWrap(True)
        self.airports.setStyleSheet(f"color: {PALETTE['text_secondary']};")
        layout.addWidget(self.airports)

        per_row = columns or len(DETAIL_FIELDS)
        details = QGridLayout()
        details.setHorizontalSpacing(24)
        details.setVerticalSpacing(2)
        self.values = {}
        for i, (key, label) in enumerate(DETAIL_FIELDS):
            row, col = (i // per_row) * 2, i % per_row
            name = QLabel(label)
            name.setFont(font_label(8))
            name.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            if row > 0:
                name.setContentsMargins(0, 6, 0, 0)
            value = QLabel("—")
            value.setFont(font(10))
            value.setStyleSheet(f"color: {PALETTE['text_primary']};")
            details.addWidget(name, row, col)
            details.addWidget(value, row + 1, col)
            self.values[key] = value
        if per_row < len(DETAIL_FIELDS):
            for col in range(per_row):
                details.setColumnStretch(col, 1)
        else:
            details.setColumnStretch(len(DETAIL_FIELDS), 1)
        layout.addLayout(details)

        self.route = QLabel("")
        self.route.setFont(font(9))
        self.route.setWordWrap(True)
        self.route.setStyleSheet(f"color: {PALETTE['text_muted']};")
        layout.addWidget(self.route)

        self.set_plan(None)

    def set_plan(self, plan, dispatch=None):
        """Shows `plan` (a core.simbrief plan dict), or hides the block if None.
        `dispatch` is FlightSessionController.dispatch_info, for the badge."""
        self.setVisible(plan is not None)
        if plan is None:
            return

        alt = plan.get("alternate")
        alt_text = f"   ALT {alt['icao']}" if alt else ""
        number = plan.get("flight_number") or "—"
        self.title.setText(
            f"{number}   {plan['origin']['icao']} → {plan['destination']['icao']}{alt_text}"
        )

        names = f"{plan['origin']['name']} → {plan['destination']['name']}"
        if alt:
            names += f"   (alternate: {alt['name']})"
        self.airports.setText(names)

        cruise = plan.get("cruise_altitude_ft")
        cargo = plan.get("cargo_kg")
        self.values["aircraft_type"].setText(plan.get("aircraft_type") or "—")
        self.values["cruise"].setText(f"{cruise:,} ft" if cruise else "—")
        self.values["distance"].setText(_fmt(plan.get("distance_nm"), " nm", 0))
        self.values["est_block"].setText(fmt_hm(plan.get("est_block_hours")))
        self.values["pax"].setText(_fmt(plan.get("pax_count")))
        self.values["cargo"].setText(f"{cargo:,} kg" if cargo is not None else "—")
        self.route.setText(plan.get("route") or "")

        if self.show_badge and dispatch is not None:
            self.badge.setText(f"DISPATCHED  ·  {dispatch['designation']} {dispatch['registration']}")
            self.badge.setVisible(True)
        else:
            self.badge.setVisible(False)