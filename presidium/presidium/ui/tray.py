"""System Tray integration for Presidium.

Provides:
- QSystemTrayIcon with app icon and context menu
- Native desktop notifications on new messages
- Minimize-to-tray on window close
- Mute modes (30 min, 1 hour, until morning, forever)
- Click notification → open specific chat
"""

from __future__ import annotations

import enum
import logging
import time
from typing import Optional, Callable

from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QIcon, QPixmap, QImage, QAction
from PySide6.QtWidgets import (
    QSystemTrayIcon,
    QMenu,
    QApplication,
    QMainWindow,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mute modes
# ---------------------------------------------------------------------------

class MuteMode(enum.IntEnum):
    """Notification mute modes."""
    OFF = 0           # Not muted
    THIRTY_MIN = 30
    ONE_HOUR = 60
    UNTIL_MORNING = 480  # 8 hours
    FOREVER = -1


# ---------------------------------------------------------------------------
# Tray Icon Manager
# ---------------------------------------------------------------------------

class TrayManager(QObject):
    """Manages the system tray icon, menu, and notifications.

    Signals:
        show_window(): Emitted when the user clicks the tray icon or "Open".
        quit_app(): Emitted when the user selects "Quit".
        open_chat(str): Emitted when a notification is clicked (peer_user_id).
    """

    show_window = Signal()
    quit_app = Signal()
    open_chat = Signal(str)

    def __init__(
        self,
        main_window: QMainWindow,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self._mute_mode = MuteMode.OFF
        self._mute_until: float = 0.0
        self._tray: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None

        # Pending notification click handler
        self._notification_chat_id: Optional[str] = None

        # Mute timer
        self._mute_timer = QTimer(self)
        self._mute_timer.setInterval(60000)  # Check every minute
        self._mute_timer.timeout.connect(self._check_mute_expiry)

    # -------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------

    def initialize(self) -> bool:
        """Set up the system tray icon and menu.

        Returns:
            True if tray is available, False otherwise.
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available.")
            return False

        # Create icon
        icon = self._create_icon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Presidium")

        # Build context menu
        self._menu = QMenu()
        self._menu.setStyleSheet("""
            QMenu {
                background-color: #1A1A24;
                color: #E0E0EC;
                border: 1px solid #3A3A50;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #242436;
            }
            QMenu::separator {
                height: 1px;
                background: #3A3A50;
                margin: 4px 8px;
            }
        """)

        # Open action
        open_action = self._menu.addAction("Open Presidium")
        open_action.triggered.connect(self._on_open)

        self._menu.addSeparator()

        # Mute submenu
        mute_menu = self._menu.addMenu("Mute Notifications")
        mute_menu.setStyleSheet(self._menu.styleSheet())

        mute_30 = mute_menu.addAction("30 minutes")
        mute_30.triggered.connect(lambda: self._set_mute(MuteMode.THIRTY_MIN))

        mute_1h = mute_menu.addAction("1 hour")
        mute_1h.triggered.connect(lambda: self._set_mute(MuteMode.ONE_HOUR))

        mute_morning = mute_menu.addAction("Until morning")
        mute_morning.triggered.connect(lambda: self._set_mute(MuteMode.UNTIL_MORNING))

        mute_forever = mute_menu.addAction("Forever")
        mute_forever.triggered.connect(lambda: self._set_mute(MuteMode.FOREVER))

        mute_off = mute_menu.addAction("Unmute")
        mute_off.triggered.connect(lambda: self._set_mute(MuteMode.OFF))

        self._menu.addSeparator()

        # Quit action
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app.emit)

        self._tray.setContextMenu(self._menu)

        # Click handler (activate = single click on most platforms)
        self._tray.activated.connect(self._on_activated)

        # Message click handler
        self._tray.messageClicked.connect(self._on_message_clicked)

        self._tray.show()
        self._mute_timer.start()

        logger.info("System tray icon initialized.")
        return True

    # -------------------------------------------------------------------
    # Icon generation
    # -------------------------------------------------------------------

    def _create_icon(self) -> QIcon:
        """Generate a simple application icon.

        Creates a small square icon with the letter "P" in accent color
        on a dark background, using Pillow if available, otherwise Qt.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            size = 64
            img = Image.new("RGBA", (size, size), (15, 15, 20, 255))
            draw = ImageDraw.Draw(img)

            # Draw accent circle
            draw.ellipse([8, 8, size - 8, size - 8], fill=(99, 102, 241, 255))

            # Draw "P" letter
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/english/Tinos-Bold.ttf", 32)
            except Exception:
                font = ImageFont.load_default()

            draw.text((18, 10), "P", fill=(255, 255, 255, 255), font=font)

            # Convert to QIcon
            from io import BytesIO
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)

            qimage = QImage()
            qimage.loadFromData(buf.read(), "PNG")
            return QIcon(QPixmap.fromImage(qimage))

        except ImportError:
            # Fallback: create a minimal icon using Qt
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            from PySide6.QtGui import QPainter, QColor
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(99, 102, 241))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(8, 8, 48, 48)
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setPointSize(28)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "P")
            painter.end()
            return QIcon(pixmap)

    # -------------------------------------------------------------------
    # Notifications
    # -------------------------------------------------------------------

    def show_notification(
        self,
        title: str,
        message: str,
        peer_user_id: str = "",
    ) -> None:
        """Show a desktop notification.

        Args:
            title: Notification title (usually contact name).
            message: Notification body (message preview).
            peer_user_id: Peer user ID for click-to-open functionality.
        """
        if self._tray is None:
            return

        # Check mute
        if self._is_muted():
            return

        self._notification_chat_id = peer_user_id if peer_user_id else None

        try:
            self._tray.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,  # 5 seconds
            )
        except Exception as exc:
            logger.debug("Notification failed: %s", exc)

    # -------------------------------------------------------------------
    # Mute management
    # -------------------------------------------------------------------

    def _set_mute(self, mode: MuteMode) -> None:
        """Set the notification mute mode."""
        self._mute_mode = mode
        if mode == MuteMode.OFF:
            self._mute_until = 0.0
            logger.info("Notifications unmuted.")
        elif mode == MuteMode.FOREVER:
            self._mute_until = float("inf")
            logger.info("Notifications muted forever.")
        else:
            self._mute_until = time.time() + (mode.value * 60)
            logger.info("Notifications muted for %d minutes.", mode.value)

    def _is_muted(self) -> bool:
        """Check if notifications are currently muted."""
        if self._mute_mode == MuteMode.OFF:
            return False
        if self._mute_mode == MuteMode.FOREVER:
            return True
        return time.time() < self._mute_until

    def _check_mute_expiry(self) -> None:
        """Check if the mute period has expired."""
        if self._mute_mode != MuteMode.OFF and self._mute_mode != MuteMode.FOREVER:
            if time.time() >= self._mute_until:
                self._mute_mode = MuteMode.OFF
                self._mute_until = 0.0
                logger.info("Mute period expired. Notifications re-enabled.")

    @property
    def is_muted(self) -> bool:
        return self._is_muted()

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon click."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_open()

    def _on_open(self) -> None:
        """Show and raise the main window."""
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()
        self.show_window.emit()

    def _on_message_clicked(self) -> None:
        """Handle notification click — open the relevant chat."""
        if self._notification_chat_id:
            self.open_chat.emit(self._notification_chat_id)
        self._on_open()

    # -------------------------------------------------------------------
    # Window close interception
    # -------------------------------------------------------------------

    def handle_close_event(self, event) -> None:
        """Intercept window close to minimize to tray instead of quitting.

        Call this from MainWindow.closeEvent().
        """
        if self._tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._main_window.hide()
            event.ignore()

            # Show a one-time notification
            if not self._notification_chat_id:
                self._tray.showMessage(
                    "Presidium",
                    "Application minimized to tray. Click to reopen.",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )
        else:
            event.accept()

    # -------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------

    def cleanup(self) -> None:
        """Remove the tray icon on quit."""
        if self._tray:
            self._tray.hide()
            self._tray = None
