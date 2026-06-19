"""Light theme definition for Presidium (Material You inspired).

Mirrors the dark theme structure with light-adapted colors for daytime use.
"""

from __future__ import annotations


class LightTheme:
    """Material You / Fluent 2 inspired light theme."""

    NAME = "light"

    # Primary colors
    BACKGROUND = "#FAFAFF"
    SURFACE = "#FFFFFF"
    SURFACE_VARIANT = "#F0F0F8"
    SURFACE_CONTAINER = "#E8E8F4"
    SURFACE_CONTAINER_HIGH = "#E0E0EE"

    # Text colors
    ON_BACKGROUND = "#1A1A2E"
    ON_SURFACE = "#24243A"
    ON_SURFACE_VARIANT = "#4A4A68"
    ON_SURFACE_DISABLED = "#A0A0B8"
    SECONDARY_TEXT = "#7070888"

    # Accent
    PRIMARY = "#6366F1"
    PRIMARY_VARIANT = "#4F46E5"
    ON_PRIMARY = "#FFFFFF"
    SECONDARY = "#0891B2"
    SECONDARY_VARIANT = "#0E7490"

    # Status colors
    SUCCESS = "#16A34A"
    WARNING = "#D97706"
    ERROR = "#DC2626"
    INFO = "#2563EB"

    # Chat bubble colors
    BUBBLE_SELF = "#6366F1"
    BUBBLE_SELF_TEXT = "#FFFFFF"
    BUBBLE_PEER = "#F0F0F8"
    BUBBLE_PEER_TEXT = "#24243A"

    # Borders and dividers
    BORDER = "#D0D0E0"
    DIVIDER = "#E8E8F0"

    # Input fields
    INPUT_BACKGROUND = "#FFFFFF"
    INPUT_BORDER = "#D0D0E0"
    INPUT_BORDER_FOCUSED = "#6366F1"

    # Sidebar
    SIDEBAR_BACKGROUND = "#F0F0F8"
    SIDEBAR_ITEM_HOVER = "#E8E8F4"
    SIDEBAR_ITEM_ACTIVE = "#E0E0EE"
    SIDEBAR_ITEM_ACTIVE_INDICATOR = "#6366F1"

    # Glassmorphism
    GLASS_BACKGROUND = "rgba(255, 255, 255, 0.85)"
    GLASS_BORDER = "rgba(208, 208, 224, 0.5)"
    GLASS_BLUR = 20

    # Shadows
    SHADOW_COLOR = "rgba(0, 0, 0, 0.08)"

    # Scrollbar
    SCROLLBAR_TRACK = "#F0F0F8"
    SCROLLBAR_HANDLE = "#C0C0D0"
    SCROLLBAR_HANDLE_HOVER = "#A0A0B8"

    # Corner radius
    CORNER_RADIUS = 16
    CORNER_RADIUS_SMALL = 8
    CORNER_RADIUS_LARGE = 24

    # Spacing
    SPACING_XS = 4
    SPACING_SM = 8
    SPACING_MD = 12
    SPACING_LG = 16
    SPACING_XL = 24
    SPACING_XXL = 32

    # Font
    FONT_FAMILY = "'Inter', 'SF Pro Display', 'Segoe UI', 'Noto Sans', sans-serif"
    FONT_FAMILY_MONO = "'Sarasa Mono SC', 'Fira Code', 'Consolas', monospace"

    FONT_SIZE_XS = 11
    FONT_SIZE_SM = 13
    FONT_SIZE_MD = 15
    FONT_SIZE_LG = 18
    FONT_SIZE_XL = 22
    FONT_SIZE_XXL = 28

    FONT_WEIGHT_NORMAL = 400
    FONT_WEIGHT_MEDIUM = 500
    FONT_WEIGHT_SEMIBOLD = 600
    FONT_WEIGHT_BOLD = 700


def get_light_stylesheet() -> str:
    """Generate a complete QSS stylesheet for the light theme."""
    t = LightTheme
    return f"""
    /* ── Global ── */
    QWidget {{
        background-color: {t.BACKGROUND};
        color: {t.ON_BACKGROUND};
        font-family: {t.FONT_FAMILY};
        font-size: {t.FONT_SIZE_MD}px;
    }}

    /* ── Main Window ── */
    QMainWindow {{
        background-color: {t.BACKGROUND};
    }}

    /* ── Sidebar ── */
    #sidebar {{
        background-color: {t.SIDEBAR_BACKGROUND};
        border-right: 1px solid {t.DIVIDER};
    }}

    #sidebar-item {{
        padding: {t.SPACING_MD}px {t.SPACING_LG}px;
        border-radius: {t.CORNER_RADIUS_SMALL}px;
        color: {t.ON_SURFACE_VARIANT};
    }}

    #sidebar-item:hover {{
        background-color: {t.SIDEBAR_ITEM_HOVER};
    }}

    #sidebar-item[active="true"] {{
        background-color: {t.SIDEBAR_ITEM_ACTIVE};
        color: {t.PRIMARY};
        border-left: 3px solid {t.SIDEBAR_ITEM_ACTIVE_INDICATOR};
    }}

    /* ── Buttons ── */
    QPushButton {{
        background-color: {t.PRIMARY};
        color: {t.ON_PRIMARY};
        border: none;
        border-radius: {t.CORNER_RADIUS_SMALL}px;
        padding: {t.SPACING_SM}px {t.SPACING_XL}px;
        font-weight: {t.FONT_WEIGHT_SEMIBOLD};
        min-height: 36px;
    }}

    QPushButton:hover {{
        background-color: {t.PRIMARY_VARIANT};
    }}

    QPushButton:pressed {{
        background-color: {t.PRIMARY};
    }}

    QPushButton:disabled {{
        background-color: {t.ON_SURFACE_DISABLED};
        color: {t.DIVIDER};
    }}

    QPushButton#secondary-button {{
        background-color: transparent;
        color: {t.PRIMARY};
        border: 1px solid {t.PRIMARY};
    }}

    QPushButton#secondary-button:hover {{
        background-color: rgba(99, 102, 241, 0.08);
    }}

    QPushButton#icon-button {{
        background-color: transparent;
        border: none;
        padding: {t.SPACING_SM}px;
        border-radius: {t.CORNER_RADIUS_SMALL}px;
        min-height: 32px;
        min-width: 32px;
    }}

    QPushButton#icon-button:hover {{
        background-color: {t.SURFACE_VARIANT};
    }}

    /* ── Input Fields ── */
    QLineEdit, QTextEdit {{
        background-color: {t.INPUT_BACKGROUND};
        border: 1px solid {t.INPUT_BORDER};
        border-radius: {t.CORNER_RADIUS_SMALL}px;
        padding: {t.SPACING_SM}px {t.SPACING_LG}px;
        color: {t.ON_SURFACE};
        selection-background-color: {t.PRIMARY};
    }}

    QLineEdit:focus, QTextEdit:focus {{
        border-color: {t.INPUT_BORDER_FOCUSED};
    }}

    /* ── Chat Bubbles ── */
    #bubble-self {{
        background-color: {t.BUBBLE_SELF};
        color: {t.BUBBLE_SELF_TEXT};
        border-radius: {t.CORNER_RADIUS}px {t.CORNER_RADIUS}px 4px {t.CORNER_RADIUS}px;
        padding: {t.SPACING_SM}px {t.SPACING_LG}px;
    }}

    #bubble-peer {{
        background-color: {t.BUBBLE_PEER};
        color: {t.BUBBLE_PEER_TEXT};
        border-radius: {t.CORNER_RADIUS}px {t.CORNER_RADIUS}px {t.CORNER_RADIUS}px 4px;
        padding: {t.SPACING_SM}px {t.SPACING_LG}px;
    }}

    /* ── Chat List ── */
    #chat-list-item {{
        padding: {t.SPACING_LG}px;
        border-bottom: 1px solid {t.DIVIDER};
    }}

    #chat-list-item:hover {{
        background-color: {t.SURFACE_VARIANT};
    }}

    /* ── Scrollbar ── */
    QScrollBar:vertical {{
        background-color: {t.SCROLLBAR_TRACK};
        width: 8px;
        margin: 0;
    }}

    QScrollBar::handle:vertical {{
        background-color: {t.SCROLLBAR_HANDLE};
        border-radius: 4px;
        min-height: 32px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: {t.SCROLLBAR_HANDLE_HOVER};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    QScrollBar:horizontal {{
        background-color: {t.SCROLLBAR_TRACK};
        height: 8px;
        margin: 0;
    }}

    QScrollBar::handle:horizontal {{
        background-color: {t.SCROLLBAR_HANDLE};
        border-radius: 4px;
        min-width: 32px;
    }}

    /* ── Labels ── */
    QLabel#title {{
        font-size: {t.FONT_SIZE_XL}px;
        font-weight: {t.FONT_WEIGHT_BOLD};
        color: {t.ON_BACKGROUND};
    }}

    QLabel#subtitle {{
        font-size: {t.FONT_SIZE_MD}px;
        color: {t.ON_SURFACE_VARIANT};
    }}

    QLabel#caption {{
        font-size: {t.FONT_SIZE_XS}px;
        color: {t.SECONDARY_TEXT};
    }}

    /* ── Glass Panel ── */
    #glass-panel {{
        background-color: {t.GLASS_BACKGROUND};
        border: 1px solid {t.GLASS_BORDER};
        border-radius: {t.CORNER_RADIUS}px;
    }}

    /* ── Status indicator ── */
    #status-online {{
        color: {t.SUCCESS};
    }}

    #status-offline {{
        color: {t.ON_SURFACE_DISABLED};
    }}

    /* ── Progress Bar ── */
    QProgressBar {{
        background-color: {t.SURFACE_VARIANT};
        border: none;
        border-radius: 4px;
        height: 6px;
    }}

    QProgressBar::chunk {{
        background-color: {t.PRIMARY};
        border-radius: 4px;
    }}

    /* ── Tooltips ── */
    QToolTip {{
        background-color: {t.SURFACE};
        color: {t.ON_SURFACE};
        border: 1px solid {t.BORDER};
        border-radius: {t.CORNER_RADIUS_SMALL}px;
        padding: {t.SPACING_SM}px;
    }}
    """
