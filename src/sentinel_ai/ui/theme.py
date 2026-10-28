"""Design tokens and the application stylesheet: the single source of truth for the UI.

Colour is used with fixed meanings. The turquoise accent is the brand and interactive colour.
Status has its own small reserved scale (good, warning, critical, plus a neutral) that is
never reused decoratively, and is always paired with an icon and a label so meaning never
rests on colour alone. Text always wears the ink tokens; status colour appears only on the
mark beside it (an icon, a dot, a bar).

Contrast was measured (WCAG 2 ratio), not eyeballed. On the card surface #1b1f25: ink 12.6:1,
muted 5.6:1, accent 5.8:1, good #5fae7a 6.2:1, warning #d9a441 7.4:1, critical #e0605a 4.7:1.
The weakest text case, muted ink on the hover surface #292f38, is 4.56:1. Status colours only
ever fill marks, never text; critical on the hover surface is 3.8:1, above the 3:1 needed
for graphics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    # surfaces, darkest to lightest
    bg: str = "#14171c"
    surface: str = "#1b1f25"
    surface_raised: str = "#232830"
    surface_hover: str = "#292f38"
    border: str = "#2c333d"
    border_strong: str = "#3a4350"
    # ink
    text: str = "#dbe1e8"
    text_muted: str = "#8d97a6"
    # brand and interaction
    accent: str = "#3aa9a0"
    accent_dim: str = "#235f5b"
    accent_wash: str = "#1a2b2d"  # selected-row tint
    # status (reserved meanings)
    good: str = "#5fae7a"
    warning: str = "#d9a441"
    warning_dim: str = "#7a5c25"
    danger: str = "#e0605a"
    danger_dim: str = "#7a302d"
    neutral: str = "#8d97a6"


PALETTE = Palette()

# 4 px base grid. Use these instead of ad-hoc numbers.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = 2  # nearly square: it sits better beside pixel art than soft rounding does

# Type. Plex Sans carries all text and every number (a display face on a figure reads as
# decoration); Plex Mono carries evidence; Pixelify Sans is reserved for the wordmark and
# small caps-style section labels, where its pixel grid echoes the duck.
FONT_UI = "IBM Plex Sans"
FONT_MONO = "IBM Plex Mono"
FONT_DISPLAY = "Pixelify Sans"
FALLBACK_UI = "Noto Sans, Ubuntu, sans-serif"
FALLBACK_MONO = "JetBrains Mono, DejaVu Sans Mono, monospace"

# role -> (family key, pixel size, weight, letter-spacing %, uppercase)
TYPE = {
    "caption": ("ui", 11, 400, 100),
    "small": ("ui", 12, 400, 100),
    "body": ("ui", 13, 400, 100),
    "body_strong": ("ui", 13, 500, 100),
    "title": ("ui", 16, 600, 100),
    "headline": ("ui", 22, 600, 100),
    "figure": ("ui", 30, 600, 100),
    "label": ("display", 12, 500, 112),  # section labels, set in capitals by the caller
    "wordmark": ("display", 18, 600, 116),
    "mono": ("mono", 12, 400, 100),
}

# Sprite colours, keyed by the single characters used in sprites.py. "." is transparent.
SPRITE_COLORS: dict[str, str] = {
    "O": "#141820",  # outline
    "d": "#3a424e",  # slate shadow
    "S": "#56606d",  # slate base
    "L": "#707c8c",  # slate light
    "H": "#97a3b3",  # slate highlight
    "A": PALETTE.accent_dim,  # teal trim
    "a": "#2f8a83",  # teal trim, bright
    "B": "#b7c1ce",  # bill and feet, lit
    "b": "#7a8696",  # bill and feet, shadow
    "V": PALETTE.accent,  # lens, lit
    "v": "#1d5753",  # lens, dim
    "h": "#a8f0e8",  # lens hot spot
    "W": PALETTE.warning,
    "w": PALETTE.warning_dim,
    "Y": "#f6dc9a",
    "R": PALETTE.danger,
    "r": PALETTE.danger_dim,
    "X": "#ffc9c4",
    "z": PALETTE.text_muted,
}


def build_stylesheet(p: Palette = PALETTE) -> str:
    return f"""
    /* No font-family or font-size here: a stylesheet font overrides every QFont set in code,
       which silently disabled the whole type scale. Fonts are set in code (ui/fonts.py). */
    QWidget {{ background: {p.bg}; color: {p.text}; }}
    QLabel {{ background: transparent; }}
    QToolTip {{ background: {p.surface_raised}; color: {p.text};
               border: 1px solid {p.border_strong}; padding: 4px 8px; }}

    QFrame#panel {{ background: {p.surface}; border: 1px solid {p.border_strong};
                    border-radius: {RADIUS}px; }}
    QFrame#card {{ background: {p.surface}; border: 1px solid {p.border};
                   border-radius: {RADIUS}px; }}
    QFrame#rule {{ background: {p.border}; border: none; max-height: 1px; min-height: 1px; }}
    QWidget#header {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
    QWidget#footer {{ background: {p.surface}; border-top: 1px solid {p.border}; }}

    QPushButton {{ background: {p.surface_raised}; border: 1px solid {p.border_strong};
                   border-radius: {RADIUS}px; padding: 7px 16px; color: {p.text}; }}
    QPushButton:hover {{ background: {p.surface_hover}; border-color: {p.accent}; }}
    QPushButton:pressed {{ background: {p.accent_wash}; }}
    QPushButton:disabled {{ color: {p.text_muted}; border-color: {p.border};
                            background: {p.surface}; }}
    QPushButton:focus {{ border-color: {p.accent}; }}
    QPushButton#primary {{ background: {p.accent_dim}; border-color: {p.accent}; color: {p.text}; }}
    QPushButton#primary:hover {{ background: #2b7570; }}
    QPushButton#primary:disabled {{ background: {p.surface}; border-color: {p.border};
                                    color: {p.text_muted}; }}
    QPushButton#link {{ background: transparent; border: none; padding: 2px 0; color: {p.accent}; }}
    QPushButton#link:hover {{ color: {p.text}; }}

    QPushButton#row {{ background: transparent; border: none; border-left: 2px solid transparent;
                       border-radius: 0; padding: 0; text-align: left; }}
    QPushButton#row:hover {{ background: {p.surface_hover}; }}
    QPushButton#row:checked {{ background: {p.accent_wash}; border-left: 2px solid {p.accent}; }}
    QPushButton#row:focus {{ background: {p.surface_hover}; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p.border_strong}; min-height: 32px; margin: 2px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QMenu {{ background: {p.surface}; border: 1px solid {p.border_strong}; padding: 4px; }}
    QMenu::item {{ padding: 6px 20px; }}
    QMenu::item:selected {{ background: {p.accent_dim}; }}
    QMenu::item:disabled {{ color: {p.text_muted}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
    """
