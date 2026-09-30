"""
ui/theme.py

Central place for colors, fonts, and the QSS stylesheet. Every other UI
file imports from here rather than hardcoding hex values.

load_fonts() (called once at app startup) registers whatever it finds in
that folder. If the folder's empty or missing, the app still runs - it
just falls back to Qt's default UI font and prints a note, rather than
crashing.

Usage:
    from ui.theme import PALETTE, load_fonts, font, build_stylesheet
    load_fonts()                        # call once, before building any UI
    app.setStyleSheet(build_stylesheet())
"""

import os
from PySide6.QtGui import QFont, QFontDatabase

_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "fonts"
)

_ROBOTO_FILES = [
    "Roboto-Light.ttf",
    "Roboto-Regular.ttf",
    "Roboto-Medium.ttf",
    "Roboto-Bold.ttf",
]

_GARAMOND_FILES = [
    "EBGaramond-Regular.ttf",
    "EBGaramond-Medium.ttf",
    "EBGaramond-Bold.ttf",
]

FONT_FAMILY = "Roboto"
_FALLBACK_FAMILY = None
_GARAMOND_FAMILY = "EB Garamond"

_fonts_loaded = False


def load_fonts():
    """
    Registers bundled Roboto .ttf files with Qt's font database, if present.
    Safe to call even if the assets/fonts folder is missing or empty - the
    app will just use the system default font instead, with a printed note
    so the gap is visible rather than silent.
    """
    global _fonts_loaded, _FALLBACK_FAMILY
    if _fonts_loaded:
        return

    if not os.path.isdir(_FONTS_DIR):
        print(f"[theme] No fonts folder at {_FONTS_DIR} - using system default font instead of Roboto.")
        _FALLBACK_FAMILY = QFont().defaultFamily()
        _fonts_loaded = True
        return

    loaded_any = False
    for filename in _ROBOTO_FILES:
        path = os.path.join(_FONTS_DIR, filename)
        if os.path.exists(path):
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id != -1:
                loaded_any = True
            else:
                print(f"[theme] Failed to load {filename} (file may be corrupt).")
        else:
            print(f"[theme] Missing {filename} - that weight will fall back to the nearest available.")

    for filename in _GARAMOND_FILES:
        path = os.path.join(_FONTS_DIR, filename)
        if os.path.exists(path):
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id == -1:
                print(f"[theme] Failed to load {filename} (file may be corrupt).")
        else:
            print(f"[theme] Missing {filename} - that weight will fall back to the nearest available.")

    if not loaded_any:
        print("[theme] No Roboto files found - using system default font instead.")
        _FALLBACK_FAMILY = QFont().defaultFamily()

    _fonts_loaded = True


def _family():
    """Returns 'Roboto' if it loaded, otherwise the system fallback."""
    return _FALLBACK_FAMILY if _FALLBACK_FAMILY else FONT_FAMILY

PALETTE = {
    "bg":               "#111115",
    "bg_sidebar":       "#111115",
    "bg_panel":         "#0f0f12",
    "bg_panel_alt":     "#0f0f12",
    "bg_input":         "#0f0f12",

    "border":           "#c9a84a",
    "border_light":     "#fbe6b1",

    "text_primary":     "#f5f6f6",
    "text_secondary":   "#6c7381",
    "text_muted":       "#606161",

    "accent":           "#e4a125",
    "accent_dim":       "#614b29",
    "accent_bg":        "#1c1812",

    "positive":         "#2d783d",
    "warning":          "#f37321",
    "negative":         "#ee2625",
}

GRADE_COLORS = {
    "A": PALETTE["positive"],
    "B": "#6f9f34",
    "C": "#d8b41f",
    "D": PALETTE["warning"],
    "F": PALETTE["negative"],
}

WEIGHT_LIGHT = QFont.Light
WEIGHT_REGULAR = QFont.Normal
WEIGHT_MEDIUM = QFont.Medium
WEIGHT_BOLD = QFont.Bold


def font(size=10, weight=WEIGHT_REGULAR):
    """General-purpose font getter - the one function most UI code will use."""
    f = QFont(_family(), size)
    f.setWeight(weight)
    return f

def garamond(size=10, weight=QFont.Normal):
    f = QFont(_GARAMOND_FAMILY, size)
    f.setWeight(weight)
    return f

def font_heading(size=26):
    return font(size, WEIGHT_LIGHT)


def font_subheading(size=11):
    return font(size, WEIGHT_REGULAR)


def font_stat_value(size=22):
    return font(size, WEIGHT_LIGHT)


def font_label(size=9):
    return font(size, WEIGHT_MEDIUM)

LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "logo.png"
)

def logo_exists():
    return os.path.exists(LOGO_PATH)


def build_stylesheet():
    p = PALETTE
    fam = _family()
    return f"""
    QMainWindow {{
        background-color: {p['bg']};
    }}
    
    QWidget#MainPanel {{
    background-color: {p['bg']};
    }}
    
    QMainWindow, QWidget {{
        color: {p['text_primary']};
        font-family: '{fam}';
    }}

    /* ---- Sidebar ---- */
    QWidget#Sidebar {{
        background-color: {p['bg_sidebar']};
        border-right: 1px solid {p['border']};
    }}

    QPushButton#NavItem {{
        background-color: transparent;
        color: {p['text_secondary']};
        text-transform: uppercase;
        border: none;
        text-align: left;
        padding: 10px 16px;
        font-family: 'EB Garamond';
        font-size: 14px;
        font-weight: 700;
    }}
    
    QPushButton#NavItem:hover {{
        color: {p['text_primary']};
        font-weight: 700;
    }}
    
    QPushButton#NavItem[active="true"] {{
        color: {p['accent']};
        border-left: 2px solid {p['accent']};
        text-transform: uppercase;
        font-weight: 700;
    }}

    /* ---- Header ---- */
    QWidget#Header {{
        background-color: {p['bg_sidebar']};
        border-bottom: 1px solid {p['border']};
    }}
    QLabel#PageTitle {{
        color: {p['text_primary']};
        font-family: 'EB Garamond';
    }}
    QLabel#PageSubtitle {{
        color: {p['text_secondary']};
        font-size: 10px;
        text-transform: uppercase;
    }}
    
    QLabel#PilotName {{
        color: {p['accent']};
        font-family: 'EB Garamond';
    }}

    /* ---- Cards / panels ---- */
    QWidget#Card {{
        background-color: {p['bg_panel']};
        border: 1px solid {p['border']};
    }}
    QLabel#SectionLabel {{
        color: {p['text_secondary']};
        font-size: 10px;
    }}

    /* ---- Stat strip ---- */
    QLabel#StatValue {{
        color: {p['text_primary']};
    }}
    QLabel#StatLabel {{
        color: {p['text_secondary']};
        font-size: 9px;
    }}

    /* ---- Badges / pills ---- */
    QLabel#Badge {{
        border: 1px solid {p['accent']};
        color: {p['accent']};
        padding: 2px 8px;
        font-size: 9px;
    }}

    /* ---- Tables ---- */
    QTableWidget {{
        background-color: {p['bg']};
        alternate-background-color: {p['bg_panel']};
        gridline-color: {p['border']};
        border: none;
        selection-background-color: {p['accent_bg']};
        selection-color: {p['accent']};
    }}
    QHeaderView::section {{
        background-color: {p['bg']};
        color: {p['text_secondary']};
        border: none;
        border-bottom: 1px solid {p['border']};
        padding: 6px;
        font-size: 10px;
    }}
    QTableWidget::item {{
        padding: 6px;
        border-bottom: 1px solid {p['border']};
    }}

    /* ---- Scrollbars (thin, unobtrusive) ---- */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border_light']};
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p['border_light']};
        min-height: 24px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal  {{
        height: 0px;
    }}
    
    /* ---- Rank progress bar ---- */
    QProgressBar#RankProgress {{
        background-color: {p['bg_input']};
        border: none;
        max-height: 10px;
        min-height: 10px;
    }}
    QProgressBar#RankProgress::chunk {{
        background-color: {p['accent']};
    }}

    /* ---- Misc ---- */
    QFrame#Divider {{
        background-color: {p['border']};
        max-height: 1px;
    }}
    
    /* ---- Buttons ---- */
    QPushButton {{
        background-color: {p['bg_input']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        padding: 7px 14px;
        font-size: 10px;
    }}

    QPushButton:hover {{
        background-color: {p['bg_panel_alt']};
        border-color: {p['border_light']};
    }}

    QPushButton:pressed {{
        background-color: {p['accent_bg']};
        border-color: {p['accent_dim']};
        color: {p['accent']};
    }}

    QPushButton:disabled {{
        background-color: {p['bg_panel']};
        color: {p['text_muted']};
        border-color: {p['border']};
    }}

    QPushButton:focus {{
        outline: none;
        border-color: {p['accent_dim']};
    }}
    
    /* ---- Table action buttons ---- */
    QPushButton#TableActionButton {{
        background-color: transparent;
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        padding: 4px 10px;
        font-size: 10px;
    }}
    
    QPushButton#TableActionButton:hover {{
        background-color: {p['accent_bg']};
        color: {p['accent']};
        border-color: {p['accent_dim']};
    }}
    
    QPushButton#TableActionButton:pressed {{
        background-color: {p['accent_dim']};
        color: {p['text_primary']};
    }}
    
    /* ---- Popups (alerts / confirmations) ---- */
    QMessageBox {{
        background-color: {p['bg_panel']};
    }}
    QMessageBox QLabel {{
        color: {p['text_primary']};
    }}
    """