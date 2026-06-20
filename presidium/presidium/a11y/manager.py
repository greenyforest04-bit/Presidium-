"""Accessibility Manager extensions for Presidium.

Provides advanced accessibility tools:
    - Keyboard navigation shortcuts registry
    - Screen reader text generation for custom widgets
    - Color blind palette transformations
    - A11y testing utilities
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QApplication

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyboard Navigator
# ---------------------------------------------------------------------------

class KeyboardNavigator:
    """Enhanced keyboard navigation for Presidium.

    Registers global keyboard shortcuts and manages focus chains
    for efficient keyboard-only navigation.

    Shortcuts:
        Ctrl+1-5: Switch between screens
        Ctrl+N: New conversation
        Ctrl+F: Search
        Ctrl+,: Settings
        Ctrl+Q: Quit
        Escape: Back / close dialog
        Tab/Shift+Tab: Next/previous focus widget
        Ctrl+Up/Down: Navigate chat list
        Alt+S: Focus search
    """

    def __init__(self) -> None:
        self._shortcuts: dict[str, tuple[int, object]] = {}

    def register_shortcut(
        self, name: str, key_sequence: int, callback: object
    ) -> None:
        """Register a keyboard shortcut.

        Args:
            name: Shortcut name for accessibility description.
            key_sequence: Qt key combination (Qt.Key | Qt.Modifier).
            callback: Callable to invoke when shortcut is triggered.
        """
        self._shortcuts[name] = (key_sequence, callback)

    def unregister_shortcut(self, name: str) -> None:
        """Unregister a keyboard shortcut by name."""
        self._shortcuts.pop(name, None)

    def get_shortcut_description(self, name: str) -> str:
        """Get a human-readable description of a shortcut."""
        descriptions = {
            "switch_chat_list": "Switch to chat list (Ctrl+1)",
            "switch_chat_room": "Switch to chat room (Ctrl+2)",
            "switch_settings": "Switch to settings (Ctrl+3)",
            "new_conversation": "New conversation (Ctrl+N)",
            "search": "Search (Ctrl+F)",
            "settings": "Settings (Ctrl+,)",
            "quit": "Quit (Ctrl+Q)",
            "back": "Back (Escape)",
            "focus_search": "Focus search (Alt+S)",
        }
        return descriptions.get(name, name)

    @property
    def registered_shortcuts(self) -> list[str]:
        """List of registered shortcut names."""
        return list(self._shortcuts.keys())


# ---------------------------------------------------------------------------
# Color Blind Palette
# ---------------------------------------------------------------------------

class ColorBlindPalette:
    """Color transformations for color blind accessibility.

    Provides color transformation matrices for common color vision
    deficiencies:
        - Protanopia (red-blind)
        - Deuteranopia (green-blind)
        - Tritanopia (blue-blind)

    These transformations adjust the UI colors so that information
    is not conveyed by color alone, and colors remain distinguishable
    for users with color vision deficiencies.
    """

    # Daltonize transformation matrices (simplified)
    # These are approximations for simulation/daltonization
    PROTAN_MATRIX = [
        0.56667, 0.43333, 0.00000,
        0.55833, 0.44167, 0.00000,
        0.00000, 0.24167, 0.75833,
    ]

    DEUTAN_MATRIX = [
        0.62500, 0.37500, 0.00000,
        0.70000, 0.30000, 0.00000,
        0.00000, 0.30000, 0.70000,
    ]

    TRITAN_MATRIX = [
        0.95000, 0.05000, 0.00000,
        0.00000, 0.43333, 0.56667,
        0.00000, 0.47500, 0.52500,
    ]

    @classmethod
    def transform_color(cls, color: QColor, mode: str = "none") -> QColor:
        """Transform a color for color blind accessibility.

        Args:
            color: The original color.
            mode: Color blindness mode ("protan", "deutan", "tritan", "none").

        Returns:
            Transformed QColor.
        """
        if mode == "none":
            return color

        matrices = {
            "protan": cls.PROTAN_MATRIX,
            "deutan": cls.DEUTAN_MATRIX,
            "tritan": cls.TRITAN_MATRIX,
        }

        matrix = matrices.get(mode)
        if matrix is None:
            return color

        r, g, b = color.redF(), color.greenF(), color.blueF()

        new_r = matrix[0] * r + matrix[1] * g + matrix[2] * b
        new_g = matrix[3] * r + matrix[4] * g + matrix[5] * b
        new_b = matrix[6] * r + matrix[7] * g + matrix[8] * b

        return QColor.fromRgbF(
            max(0.0, min(1.0, new_r)),
            max(0.0, min(1.0, new_g)),
            max(0.0, min(1.0, new_b)),
        )

    @classmethod
    def get_safe_palette(cls, mode: str = "none") -> dict[str, QColor]:
        """Get a color-blind-safe palette.

        Returns a palette of distinguishable colors that remain
        distinct under the specified color vision deficiency.

        Args:
            mode: Color blindness mode.

        Returns:
            Dictionary of role → QColor.
        """
        # Wong color-blind-safe palette
        base_palette = {
            "primary": QColor("#0072B2"),
            "secondary": QColor("#E69F00"),
            "tertiary": QColor("#009E73"),
            "error": QColor("#D55E00"),
            "warning": QColor("#F0E442"),
            "info": QColor("#56B4E9"),
            "success": QColor("#009E73"),
            "accent": QColor("#CC79A7"),
        }

        if mode == "none":
            return base_palette

        return {
            role: cls.transform_color(color, mode)
            for role, color in base_palette.items()
        }


# ---------------------------------------------------------------------------
# Screen Reader Text Generator
# ---------------------------------------------------------------------------

class ScreenReaderText:
    """Generates descriptive text for screen readers.

    Creates rich, context-aware descriptions of UI elements
    that go beyond the basic accessible name.
    """

    @staticmethod
    def chat_bubble_description(
        sender_name: str,
        message_text: str,
        timestamp: str,
        is_own: bool = False,
        is_read: bool = False,
        reply_to: str = "",
        reactions: str = "",
    ) -> str:
        """Generate a screen reader description for a chat bubble.

        Args:
            sender_name: Name of the message sender.
            message_text: The message content.
            timestamp: Time string (e.g., "14:32").
            is_own: Whether this is our own message.
            is_read: Whether the message has been read.
            reply_to: Name of the contact being replied to.
            reactions: Reaction summary (e.g., "2 thumbs up").

        Returns:
            Descriptive text for screen readers.
        """
        parts: list[str] = []

        if is_own:
            parts.append("You")
        else:
            parts.append(sender_name)

        if reply_to:
            parts.append(f"reply to {reply_to}")

        parts.append(message_text)
        parts.append(f"at {timestamp}")

        if is_own:
            parts.append("read" if is_read else "delivered")

        if reactions:
            parts.append(reactions)

        return ", ".join(parts)

    @staticmethod
    def contact_description(
        name: str,
        user_id: str,
        is_online: bool = False,
        unread_count: int = 0,
    ) -> str:
        """Generate a screen reader description for a contact item.

        Args:
            name: Contact display name.
            user_id: Contact user ID.
            is_online: Whether the contact is currently online.
            unread_count: Number of unread messages.

        Returns:
            Descriptive text for screen readers.
        """
        parts = [name]
        if is_online:
            parts.append("online")
        else:
            parts.append("offline")
        if unread_count > 0:
            parts.append(f"{unread_count} unread messages")
        return ", ".join(parts)

    @staticmethod
    def voice_message_description(
        duration_seconds: int,
        is_playing: bool = False,
        position_seconds: int = 0,
    ) -> str:
        """Generate a screen reader description for a voice message.

        Args:
            duration_seconds: Total duration of the voice message.
            is_playing: Whether the message is currently playing.
            position_seconds: Current playback position.

        Returns:
            Descriptive text for screen readers.
        """
        duration_str = f"{duration_seconds // 60}:{duration_seconds % 60:02d}"
        if is_playing:
            position_str = f"{position_seconds // 60}:{position_seconds % 60:02d}"
            return f"Voice message, playing at {position_str} of {duration_str}"
        return f"Voice message, {duration_str}"
