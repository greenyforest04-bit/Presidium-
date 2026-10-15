from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Colors:
    BG_PRIMARY: str = "#0F0F0F"
    BG_SECONDARY: str = "#18181B"
    BG_TERTIARY: str = "#27272A"
    BG_ELEVATED: str = "#1C1C1F"
    ACCENT: str = "#6366F1"
    ACCENT_HOVER: str = "#818CF8"
    ACCENT_PRESSED: str = "#4F46E5"
    ACCENT_GLOW: str = "rgba(99, 102, 241, 0.15)"
    TEXT_PRIMARY: str = "#FAFAFA"
    TEXT_SECONDARY: str = "#A1A1AA"
    TEXT_TERTIARY: str = "#71717A"
    TEXT_MUTED: str = "#52525B"
    SUCCESS: str = "#22C55E"
    WARNING: str = "#F59E0B"
    ERROR: str = "#EF4444"
    INFO: str = "#3B82F6"
    BUBBLE_OWN: str = "#312E81"
    BUBBLE_OTHER: str = "#27272A"
    BUBBLE_OWN_TAIL: str = "#312E81"
    BUBBLE_OTHER_TAIL: str = "#27272A"
    GLASS_BG: str = "rgba(24, 24, 27, 0.85)"
    GLASS_BORDER: str = "rgba(255, 255, 255, 0.08)"


@dataclass(frozen=True)
class Spacing:
    XS: int = 4
    SM: int = 8
    MD: int = 12
    LG: int = 16
    XL: int = 24
    XXL: int = 32
    XXXL: int = 48


@dataclass(frozen=True)
class Typography:
    FONT_FAMILY: str = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    H1: Tuple[str, int, float] = ("24px", 600, -0.02)
    H2: Tuple[str, int, float] = ("18px", 600, -0.01)
    H3: Tuple[str, int, float] = ("16px", 600, -0.01)
    BODY: Tuple[str, int, float] = ("14px", 400, 0.0)
    CAPTION: Tuple[str, int, float] = ("12px", 500, 0.01)
    OVERLINE: Tuple[str, int, float] = ("10px", 600, 0.05)
    BUTTON: Tuple[str, int, float] = ("14px", 500, 0.01)


@dataclass(frozen=True)
class Shadows:
    SM: str = "0 1px 2px rgba(0,0,0,0.3)"
    MD: str = "0 4px 12px rgba(0,0,0,0.4)"
    LG: str = "0 8px 24px rgba(0,0,0,0.5)"
    GLOW: str = "0 0 20px rgba(99,102,241,0.2)"


@dataclass(frozen=True)
class Radius:
    SM: int = 6
    MD: int = 10
    LG: int = 16
    XL: int = 20
    FULL: int = 9999


@dataclass(frozen=True)
class LightColors:
    BG_PRIMARY: str = "#FFFFFF"
    BG_SECONDARY: str = "#F4F4F5"
    BG_TERTIARY: str = "#E4E4E7"
    BG_ELEVATED: str = "#FAFAFA"
    TEXT_PRIMARY: str = "#18181B"
    TEXT_SECONDARY: str = "#52525B"
    TEXT_TERTIARY: str = "#71717A"
    TEXT_MUTED: str = "#A1A1AA"
    BUBBLE_OWN: str = "#EEF2FF"
    BUBBLE_OTHER: str = "#F4F4F5"
    GLASS_BG: str = "rgba(255, 255, 255, 0.85)"
    GLASS_BORDER: str = "rgba(0, 0, 0, 0.08)"


TOKENS = Colors()
SPACE = Spacing()
TYPE = Typography()
SHADOW = Shadows()
RADIUS = Radius()
