"""Screen security overlay for Presidium.

Provides:
- Screenshot detection heuristic (monitoring /tmp on Linux, Desktop on macOS)
- Chat blur on screenshot detection (5-second blur)
- App lock via PIN or system password
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from pathlib import Path
from typing import Optional, Callable

from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Screenshot detection
# ---------------------------------------------------------------------------

class ScreenshotDetector(QObject):
    """Heuristic screenshot detection for screen security.

    Monitors common screenshot save locations for new files that
    appear while the app is in focus. This is a heuristic approach
    and may produce false positives or miss screenshots.

    Signals:
        screenshot_detected(): Emitted when a potential screenshot is detected.
    """

    screenshot_detected = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._monitor_dirs: list[Path] = []
        self._known_files: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)  # Check every 2 seconds
        self._timer.timeout.connect(self._check_for_screenshots)
        self._enabled = False

    def start(self) -> None:
        """Start monitoring for screenshots."""
        self._monitor_dirs = self._detect_screenshot_dirs()
        self._snapshot_known_files()
        self._enabled = True
        self._timer.start()
        logger.info("Screenshot detector started. Monitoring %d directories.", len(self._monitor_dirs))

    def stop(self) -> None:
        """Stop monitoring."""
        self._timer.stop()
        self._enabled = False

    def _detect_screenshot_dirs(self) -> list[Path]:
        """Detect platform-specific screenshot directories."""
        dirs: list[Path] = []
        system = platform.system()

        if system == "Linux":
            # Common screenshot locations
            home = Path.home()
            dirs.extend([
                home / "Pictures",
                home / "Pictures" / "Screenshots",
                home / "Downloads",
                Path("/tmp"),
            ])
        elif system == "Darwin":  # macOS
            home = Path.home()
            dirs.extend([
                home / "Desktop",
                home / "Pictures" / "Screenshots",
                home / "Downloads",
            ])
        elif system == "Windows":
            home = Path.home()
            dirs.extend([
                home / "Pictures" / "Screenshots",
                home / "Desktop",
                home / "Downloads",
            ])

        # Only include directories that actually exist
        return [d for d in dirs if d.exists()]

    def _snapshot_known_files(self) -> None:
        """Take a snapshot of existing files in monitor directories."""
        self._known_files.clear()
        for dir_path in self._monitor_dirs:
            try:
                for f in dir_path.iterdir():
                    if f.is_file():
                        self._known_files.add(f.name)
            except PermissionError:
                pass

    def _check_for_screenshots(self) -> None:
        """Check for new files in screenshot directories."""
        if not self._enabled:
            return

        for dir_path in self._monitor_dirs:
            try:
                for f in dir_path.iterdir():
                    if f.is_file() and f.name not in self._known_files:
                        # Check if it's an image file
                        suffix = f.suffix.lower()
                        if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"):
                            self._known_files.add(f.name)
                            # Check if the file was created very recently (last 5 seconds)
                            try:
                                mtime = f.stat().st_mtime
                                if time.time() - mtime < 5.0:
                                    logger.warning("Potential screenshot detected: %s", f.name)
                                    self.screenshot_detected.emit()
                            except OSError:
                                pass
            except (PermissionError, OSError):
                pass


# ---------------------------------------------------------------------------
# Security blur overlay
# ---------------------------------------------------------------------------

class SecurityBlurOverlay(QWidget):
    """A semi-transparent overlay that blurs the chat content when triggered.

    Used when a screenshot is detected to obscure sensitive content.
    Automatically removes itself after a configurable duration.
    """

    def __init__(
        self,
        parent: QWidget,
        blur_duration: float = 5.0,
    ) -> None:
        super().__init__(parent)
        self._duration = blur_duration
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

        self.setStyleSheet("""
            background-color: rgba(15, 15, 20, 230);
            border-radius: 16px;
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel("Screenshot detected")
        label.setStyleSheet("""
            color: #F87171;
            font-size: 18px;
            font-weight: bold;
        """)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

        sublabel = QLabel("Content hidden for security")
        sublabel.setStyleSheet("color: #8888A8; font-size: 14px;")
        sublabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sublabel)

    def activate(self) -> None:
        """Show the blur overlay and start the auto-hide timer."""
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self._timer.start(int(self._duration * 1000))

    def deactivate(self) -> None:
        """Hide the blur overlay immediately."""
        self._timer.stop()
        self.hide()


# ---------------------------------------------------------------------------
# PIN Lock Screen
# ---------------------------------------------------------------------------

class PinLockScreen(QWidget):
    """A PIN lock screen that blocks access to the app.

    Shows a numeric PIN input. On correct PIN entry, the lock is removed.

    Signals:
        unlocked(): Emitted when the correct PIN is entered.
    """

    unlocked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pin: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("Presidium Locked")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #E0E0EC;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enter your PIN to unlock")
        subtitle.setStyleSheet("font-size: 14px; color: #8888A8;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        self._pin_input = QLineEdit()
        self._pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin_input.setMaxLength(32)
        self._pin_input.setPlaceholderText("PIN")
        self._pin_input.setMinimumHeight(48)
        self._pin_input.setMaximumWidth(300)
        self._pin_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pin_input.returnPressed.connect(self._on_submit)
        layout.addWidget(self._pin_input, alignment=Qt.AlignmentFlag.AlignCenter)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #F87171; font-size: 12px;")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._error_label)

        unlock_btn = QPushButton("Unlock")
        unlock_btn.setMinimumWidth(200)
        unlock_btn.setMinimumHeight(44)
        unlock_btn.clicked.connect(self._on_submit)
        layout.addWidget(unlock_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_pin(self, pin: str) -> None:
        """Set the PIN for unlocking."""
        self._pin = pin

    def _on_submit(self) -> None:
        """Check the entered PIN."""
        entered = self._pin_input.text()
        if self._pin is None:
            # No PIN set — just unlock
            self._pin_input.clear()
            self._error_label.setText("")
            self.unlocked.emit()
            return

        import hmac
        if hmac.compare_digest(entered, self._pin):
            self._pin_input.clear()
            self._error_label.setText("")
            self.unlocked.emit()
        else:
            self._error_label.setText("Incorrect PIN. Try again.")
            self._pin_input.clear()
