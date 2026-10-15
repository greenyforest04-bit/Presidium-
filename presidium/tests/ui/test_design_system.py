from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError


def test_tokens_immutable():
    from presidium.ui.design_system.tokens import TOKENS
    with pytest.raises(FrozenInstanceError):
        TOKENS.ACCENT = "#000000"


def test_tokens_accent_value():
    from presidium.ui.design_system.tokens import TOKENS
    assert TOKENS.ACCENT == "#6366F1"


def test_spacing_multiple_of_4():
    from presidium.ui.design_system.tokens import SPACE
    for field_name in ("XS", "SM", "MD", "LG", "XL", "XXL", "XXXL"):
        val = getattr(SPACE, field_name)
        assert val % 4 == 0, f"{field_name}={val} is not a multiple of 4"


def test_radius_values():
    from presidium.ui.design_system.tokens import RADIUS
    assert RADIUS.SM == 6
    assert RADIUS.MD == 10
    assert RADIUS.LG == 16
    assert RADIUS.XL == 20
    assert RADIUS.FULL == 9999


def test_typography_font_family():
    from presidium.ui.design_system.tokens import TYPE
    assert "Inter" in TYPE.FONT_FAMILY
    assert len(TYPE.H1) == 3
    assert len(TYPE.BODY) == 3


def test_light_colors_exist():
    from presidium.ui.design_system.tokens import LightColors
    colors = LightColors()
    assert colors.BG_PRIMARY == "#FFFFFF"
    assert colors.TEXT_PRIMARY == "#18181B"


def test_shadows_format():
    from presidium.ui.design_system.tokens import SHADOW
    assert "rgba" in SHADOW.SM or "box-shadow" in SHADOW.SM


def test_animations_import():
    from presidium.ui.design_system.animations import (
        fade_in, slide_up, spring_scale, shimmer, press_button, AnimationGroup,
    )
    assert callable(fade_in)
    assert callable(slide_up)
    assert callable(spring_scale)
    assert callable(shimmer)
    assert callable(press_button)


def test_contrast_ratio():
    from presidium.ui.design_system.tokens import TOKENS

    def luminance(hex_color: str) -> float:
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16) / 255.0, int(hex_color[2:4], 16) / 255.0, int(hex_color[4:6], 16) / 255.0
        def linearize(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)

    bg = luminance(TOKENS.BG_PRIMARY)
    txt = luminance(TOKENS.TEXT_PRIMARY)
    lighter = max(bg, txt)
    darker = min(bg, txt)
    ratio = (lighter + 0.05) / (darker + 0.05)
    assert ratio >= 4.5, f"Contrast ratio {ratio:.2f}:1 is below WCAG AA minimum of 4.5:1"


def test_all_tokens_defined():
    from presidium.ui.design_system.tokens import Colors
    required = [
        "BG_PRIMARY", "BG_SECONDARY", "BG_TERTIARY", "BG_ELEVATED",
        "ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED", "ACCENT_GLOW",
        "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_TERTIARY", "TEXT_MUTED",
        "SUCCESS", "WARNING", "ERROR", "INFO",
        "BUBBLE_OWN", "BUBBLE_OTHER", "BUBBLE_OWN_TAIL", "BUBBLE_OTHER_TAIL",
        "GLASS_BG", "GLASS_BORDER",
    ]
    for field in required:
        assert hasattr(Colors, field), f"Missing Colors field: {field}"
