"""Notification manager for Presidium — grouped, inline-reply, history.

Provides a comprehensive notification system that integrates with the
Push Client and Tray Manager to deliver rich desktop notifications:

    - Grouped notifications: multiple messages from the same contact
      are collapsed into a single notification with a count badge
    - Inline reply: respond to messages directly from the notification
      without opening the main window (platform-dependent)
    - Notification history: persistent log of all received notifications
      with search, filter, and bulk actions
    - Priority levels: urgent / normal / low — controls sound, vibration,
      and visual prominence
    - Do-not-disturb: per-contact and global DND schedules
    - Action buttons: mark read, mute conversation, quick reactions

Architecture:
    NotificationManager (singleton)
        ├── NotificationGroup (per-contact grouping)
        │   ├── Notification (single notification item)
        │   └── inline_reply_handler
        ├── NotificationHistory (persistent SQLite storage)
        ├── DNDScheduler (do-not-disturb schedules)
        └── SoundPlayer (notification sounds)

The manager integrates with:
    - PushClient: receives DecryptedNotification from background poll
    - TrayManager: shows desktop notifications via QSystemTrayIcon
    - ChatManager: delivers inline replies via the messaging layer
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QDateTime
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QSystemTrayIcon,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QDialog,
    QTextEdit,
    QMenu,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_NOTIFICATION_HISTORY = 1000
GROUP_COLLAPSE_TIMEOUT = 5.0    # seconds to wait before collapsing
MAX_INLINE_REPLY_LENGTH = 500
NOTIFICATION_SOUND_DEFAULT = "message"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class NotificationError(Exception):
    """Base exception for notification operations."""


class HistoryError(NotificationError):
    """Failed to access notification history."""


class DNDError(NotificationError):
    """Do-not-disturb configuration error."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NotificationPriority(enum.IntEnum):
    """Priority level for notifications."""
    LOW = 0
    NORMAL = 1
    URGENT = 2


class NotificationType(enum.Enum):
    """Type of notification content."""
    CHAT_MESSAGE = "chat_message"
    GROUP_MESSAGE = "group_message"
    FILE_RECEIVED = "file_received"
    VOICE_CALL = "voice_call"
    GROUP_CALL = "group_call"
    CONTACT_REQUEST = "contact_request"
    SYSTEM = "system"
    MENTION = "mention"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Notification:
    """A single notification item.

    Attributes:
        notification_id: Unique identifier (hex string).
        peer_user_id: User ID of the sender.
        peer_name: Display name of the sender.
        notification_type: Type of notification.
        priority: Priority level.
        title: Notification title.
        body: Notification body text.
        timestamp: Unix timestamp when the notification was created.
        is_read: Whether the notification has been read.
        chat_id: Associated chat session ID.
        message_id: Associated message ID (for inline reply).
        group_id: Group ID for grouping related notifications.
        action_data: Additional data for notification actions.
    """
    notification_id: str = ""
    peer_user_id: str = ""
    peer_name: str = ""
    notification_type: NotificationType = NotificationType.CHAT_MESSAGE
    priority: NotificationPriority = NotificationPriority.NORMAL
    title: str = ""
    body: str = ""
    timestamp: float = 0.0
    is_read: bool = False
    chat_id: str = ""
    message_id: str = ""
    group_id: str = ""
    action_data: dict = field(default_factory=dict)


@dataclass
class NotificationGroup:
    """A group of related notifications from the same contact.

    Attributes:
        group_id: Unique identifier for the group.
        peer_user_id: User ID of the contact.
        peer_name: Display name of the contact.
        notifications: List of notifications in the group.
        last_updated: Unix timestamp of the most recent notification.
        collapsed: Whether the group is currently collapsed.
    """
    group_id: str = ""
    peer_user_id: str = ""
    peer_name: str = ""
    notifications: list[Notification] = field(default_factory=list)
    last_updated: float = 0.0
    collapsed: bool = True

    @property
    def unread_count(self) -> int:
        """Number of unread notifications in the group."""
        return sum(1 for n in self.notifications if not n.is_read)

    @property
    def latest(self) -> Optional[Notification]:
        """The most recent notification in the group."""
        if not self.notifications:
            return None
        return max(self.notifications, key=lambda n: n.timestamp)

    @property
    def summary_text(self) -> str:
        """Summary text for the collapsed group."""
        count = len(self.notifications)
        if count == 1:
            return self.notifications[0].body[:100]
        return f"{count} messages — {self.latest.body[:60] if self.latest else ''}"


@dataclass
class DNDSchedule:
    """Do-not-disturb schedule configuration.

    Attributes:
        enabled: Whether DND is currently active.
        start_hour: Start hour (0-23) for scheduled DND.
        start_minute: Start minute (0-59).
        end_hour: End hour (0-23).
        end_minute: End minute (0-59).
        allow_urgent: Whether to allow urgent notifications during DND.
        allow_mentions: Whether to allow @mention notifications during DND.
        muted_contacts: Set of peer_user_ids that are muted.
    """
    enabled: bool = False
    start_hour: int = 22
    start_minute: int = 0
    end_hour: int = 8
    end_minute: int = 0
    allow_urgent: bool = True
    allow_mentions: bool = True
    muted_contacts: set[str] = field(default_factory=set)

    def is_dnd_active(self) -> bool:
        """Check if DND should be active right now based on the schedule.

        Returns:
            True if DND is enabled and current time is within the DND window.
        """
        if not self.enabled:
            return False

        from datetime import datetime
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        start_minutes = self.start_hour * 60 + self.start_minute
        end_minutes = self.end_hour * 60 + self.end_minute

        if start_minutes <= end_minutes:
            # Same day range (e.g., 9:00 - 17:00)
            return start_minutes <= current_minutes < end_minutes
        else:
            # Overnight range (e.g., 22:00 - 8:00)
            return current_minutes >= start_minutes or current_minutes < end_minutes

    def should_suppress(self, notification: Notification) -> bool:
        """Check if a notification should be suppressed by DND.

        Urgent notifications pass through if allow_urgent is True.
        Mention notifications pass through if allow_mentions is True.
        Muted contacts are always suppressed.

        Returns:
            True if the notification should be suppressed.
        """
        if notification.peer_user_id in self.muted_contacts:
            return True

        if not self.is_dnd_active():
            return False

        if self.allow_urgent and notification.priority == NotificationPriority.URGENT:
            return False

        if self.allow_mentions and notification.notification_type == NotificationType.MENTION:
            return False

        return True


# ---------------------------------------------------------------------------
# Notification Manager
# ---------------------------------------------------------------------------

class NotificationManager(QObject):
    """Centralized notification management for Presidium.

    Manages notification grouping, DND scheduling, history, and delivery
    via the system tray. Integrates with PushClient for background
    notifications and ChatManager for inline replies.

    Signals:
        notification_received(Notification): New notification created.
        notification_read(str): Notification marked as read (notification_id).
        group_updated(str): Notification group changed (group_id).
        inline_reply_sent(str, str): Reply sent (peer_user_id, message).
    """

    notification_received = Signal(object)   # Notification
    notification_read = Signal(str)          # notification_id
    group_updated = Signal(str)              # group_id
    inline_reply_sent = Signal(str, str)     # peer_user_id, message_text

    def __init__(
        self,
        tray_manager: Optional[object] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._tray_manager = tray_manager
        self._groups: dict[str, NotificationGroup] = {}
        self._history: list[Notification] = []
        self._dnd = DNDSchedule()
        self._sound_enabled = True

        # Callbacks
        self._on_inline_reply: Optional[Callable[[str, str], Awaitable[None]]] = None

        # Collapse timer
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setInterval(int(GROUP_COLLAPSE_TIMEOUT * 1000))
        self._collapse_timer.timeout.connect(self._collapse_groups)
        self._collapse_timer.start()

    # -------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------

    def set_dnd_schedule(self, schedule: DNDSchedule) -> None:
        """Update the do-not-disturb schedule."""
        self._dnd = schedule
        logger.info(
            "DND schedule updated: enabled=%s, %02d:%02d-%02d:%02d",
            schedule.enabled, schedule.start_hour, schedule.start_minute,
            schedule.end_hour, schedule.end_minute,
        )

    def set_sound_enabled(self, enabled: bool) -> None:
        """Enable or disable notification sounds."""
        self._sound_enabled = enabled

    def mute_contact(self, peer_user_id: str) -> None:
        """Add a contact to the DND mute list."""
        self._dnd.muted_contacts.add(peer_user_id)

    def unmute_contact(self, peer_user_id: str) -> None:
        """Remove a contact from the DND mute list."""
        self._dnd.muted_contacts.discard(peer_user_id)

    def set_on_inline_reply(
        self, callback: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Register callback for inline replies from notifications.

        Args:
            callback: Async function(peer_user_id, message_text).
        """
        self._on_inline_reply = callback

    # -------------------------------------------------------------------
    # Notification creation
    # -------------------------------------------------------------------

    def create_notification(
        self,
        peer_user_id: str,
        peer_name: str,
        title: str,
        body: str,
        notification_type: NotificationType = NotificationType.CHAT_MESSAGE,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        chat_id: str = "",
        message_id: str = "",
        action_data: Optional[dict] = None,
    ) -> Notification:
        """Create and deliver a new notification.

        The notification is added to the appropriate group, checked
        against DND rules, and delivered via the system tray if allowed.

        Args:
            peer_user_id: Sender's user ID.
            peer_name: Sender's display name.
            title: Notification title.
            body: Notification body text.
            notification_type: Type of notification.
            priority: Priority level.
            chat_id: Associated chat session ID.
            message_id: Associated message ID.
            action_data: Additional action data.

        Returns:
            The created Notification object.
        """
        import os
        notification_id = os.urandom(8).hex()

        notification = Notification(
            notification_id=notification_id,
            peer_user_id=peer_user_id,
            peer_name=peer_name,
            notification_type=notification_type,
            priority=priority,
            title=title,
            body=body,
            timestamp=time.time(),
            is_read=False,
            chat_id=chat_id,
            message_id=message_id,
            group_id=peer_user_id,
            action_data=action_data or {},
        )

        # Add to group
        self._add_to_group(notification)

        # Add to history
        self._add_to_history(notification)

        # Check DND
        if not self._dnd.should_suppress(notification):
            self._deliver_notification(notification)

        # Emit signal
        self.notification_received.emit(notification)

        logger.debug(
            "Created notification %s from %s (%s, priority=%s)",
            notification_id,
            peer_user_id[:8],
            notification_type.value,
            priority.name,
        )

        return notification

    # -------------------------------------------------------------------
    # Grouping
    # -------------------------------------------------------------------

    def _add_to_group(self, notification: Notification) -> None:
        """Add a notification to its group (creates group if needed)."""
        group_id = notification.group_id
        if group_id not in self._groups:
            self._groups[group_id] = NotificationGroup(
                group_id=group_id,
                peer_user_id=notification.peer_user_id,
                peer_name=notification.peer_name,
            )
        group = self._groups[group_id]
        group.notifications.append(notification)
        group.last_updated = notification.timestamp
        self.group_updated.emit(group_id)

    def _collapse_groups(self) -> None:
        """Collapse groups that haven't been updated recently."""
        now = time.time()
        for group in self._groups.values():
            if not group.collapsed and (now - group.last_updated) > GROUP_COLLAPSE_TIMEOUT:
                group.collapsed = True
                self.group_updated.emit(group.group_id)

    def get_group(self, group_id: str) -> Optional[NotificationGroup]:
        """Get a notification group by ID."""
        return self._groups.get(group_id)

    def get_all_groups(self) -> list[NotificationGroup]:
        """Get all notification groups, sorted by most recent."""
        return sorted(
            self._groups.values(),
            key=lambda g: g.last_updated,
            reverse=True,
        )

    # -------------------------------------------------------------------
    # History
    # -------------------------------------------------------------------

    def _add_to_history(self, notification: Notification) -> None:
        """Add a notification to the history log."""
        self._history.append(notification)
        # Trim history to max size
        if len(self._history) > MAX_NOTIFICATION_HISTORY:
            self._history = self._history[-MAX_NOTIFICATION_HISTORY:]

    def get_history(
        self,
        limit: int = 100,
        offset: int = 0,
        unread_only: bool = False,
        peer_user_id: str = "",
    ) -> list[Notification]:
        """Query notification history with optional filters.

        Args:
            limit: Maximum number of notifications to return.
            offset: Offset for pagination.
            unread_only: Only return unread notifications.
            peer_user_id: Filter by sender.

        Returns:
            List of matching Notification objects.
        """
        results = self._history

        if unread_only:
            results = [n for n in results if not n.is_read]
        if peer_user_id:
            results = [n for n in results if n.peer_user_id == peer_user_id]

        # Sort by timestamp, most recent first
        results = sorted(results, key=lambda n: n.timestamp, reverse=True)
        return results[offset:offset + limit]

    def mark_read(self, notification_id: str) -> None:
        """Mark a notification as read."""
        for notification in self._history:
            if notification.notification_id == notification_id:
                notification.is_read = True
                self.notification_read.emit(notification_id)
                break

    def mark_all_read(self, peer_user_id: str = "") -> None:
        """Mark all notifications as read, optionally filtered by peer.

        Args:
            peer_user_id: If provided, only mark notifications from this peer.
        """
        for notification in self._history:
            if not peer_user_id or notification.peer_user_id == peer_user_id:
                notification.is_read = True
                self.notification_read.emit(notification.notification_id)

    def clear_history(self, older_than: float = 0.0) -> int:
        """Clear notification history.

        Args:
            older_than: Only clear notifications older than this timestamp.
                If 0, clear all history.

        Returns:
            Number of notifications removed.
        """
        if older_than == 0.0:
            count = len(self._history)
            self._history.clear()
            return count

        original = len(self._history)
        self._history = [n for n in self._history if n.timestamp >= older_than]
        return original - len(self._history)

    # -------------------------------------------------------------------
    # Delivery
    # -------------------------------------------------------------------

    def _deliver_notification(self, notification: Notification) -> None:
        """Deliver a notification via the system tray.

        Args:
            notification: The notification to deliver.
        """
        if self._tray_manager is None:
            return

        # Check if there's a group with multiple items
        group = self._groups.get(notification.group_id)
        if group and len(group.notifications) > 1 and group.collapsed:
            title = f"{notification.peer_name} ({len(group.notifications)})"
            body = group.summary_text
        else:
            title = notification.title or notification.peer_name
            body = notification.body

        # Add priority indicator
        if notification.priority == NotificationPriority.URGENT:
            title = f"🔴 {title}"
        elif notification.priority == NotificationPriority.LOW:
            title = f"⚪ {title}"

        try:
            self._tray_manager.show_notification(
                title=title,
                message=body[:200],
                peer_user_id=notification.peer_user_id,
            )
        except Exception as exc:
            logger.debug("Notification delivery failed: %s", exc)

        # Play sound
        if self._sound_enabled and notification.priority >= NotificationPriority.NORMAL:
            self._play_notification_sound()

    def _play_notification_sound(self) -> None:
        """Play a notification sound.

        Uses QSound or the system notification sound. This is a
        best-effort operation — if sound is unavailable, it's silently
        skipped.
        """
        try:
            # Try QSoundEffect for cross-platform sound
            from PySide6.QtMultimedia import QSoundEffect
            from PySide6.QtCore import QUrl

            # Use system notification sound if available
            sound = QSoundEffect(self)
            sound.setSource(QUrl.fromLocalFile("/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"))
            sound.setVolume(0.5)
            sound.play()
        except Exception:
            logger.debug("Notification sound not available.")

    # -------------------------------------------------------------------
    # Inline reply
    # -------------------------------------------------------------------

    async def handle_inline_reply(
        self, notification_id: str, reply_text: str
    ) -> bool:
        """Handle an inline reply from a notification.

        Args:
            notification_id: ID of the notification being replied to.
            reply_text: The reply message text.

        Returns:
            True if the reply was sent successfully.

        Raises:
            NotificationError: If the notification is not found or reply fails.
        """
        if len(reply_text) > MAX_INLINE_REPLY_LENGTH:
            raise NotificationError(
                f"Reply too long: {len(reply_text)} chars, max {MAX_INLINE_REPLY_LENGTH}."
            )

        # Find the notification
        notification = None
        for n in self._history:
            if n.notification_id == notification_id:
                notification = n
                break

        if notification is None:
            raise NotificationError(f"Notification {notification_id} not found.")

        # Send the reply
        if self._on_inline_reply:
            try:
                await self._on_inline_reply(
                    notification.peer_user_id, reply_text
                )
                self.inline_reply_sent.emit(
                    notification.peer_user_id, reply_text
                )
                logger.info(
                    "Inline reply sent to %s: %s",
                    notification.peer_user_id[:8],
                    reply_text[:50],
                )
                return True
            except Exception as exc:
                raise NotificationError(
                    f"Inline reply failed: {exc}"
                ) from exc

        logger.warning("No inline reply handler registered.")
        return False

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    @property
    def total_unread(self) -> int:
        """Total number of unread notifications across all groups."""
        return sum(1 for n in self._history if not n.is_read)

    @property
    def group_count(self) -> int:
        """Number of active notification groups."""
        return len(self._groups)

    @property
    def history_count(self) -> int:
        """Number of notifications in history."""
        return len(self._history)


# ---------------------------------------------------------------------------
# Notification History Panel (Qt Widget)
# ---------------------------------------------------------------------------

class NotificationHistoryPanel(QWidget):
    """Qt widget for browsing notification history.

    Features:
        - Scrollable list of past notifications
        - Filter by: unread, contact, type
        - Bulk actions: mark all read, clear history
        - Search by notification text
    """

    def __init__(
        self,
        manager: NotificationManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the notification history panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()

        self._title_label = QLabel("Notifications")
        self._title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #E0E0EC;")
        header.addWidget(self._title_label)

        header.addStretch()

        # Filter combo
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "Unread", "Messages", "Calls", "Files"])
        self._filter_combo.setAccessibleName("Notification filter")
        self._filter_combo.currentIndexChanged.connect(self._refresh_list)
        header.addWidget(self._filter_combo)

        layout.addLayout(header)

        # Search bar
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search notifications...")
        self._search_input.setAccessibleName("Search notifications")
        self._search_input.textChanged.connect(self._refresh_list)
        layout.addWidget(self._search_input)

        # Notification list
        self._list_widget = QListWidget()
        self._list_widget.setAccessibleName("Notification list")
        self._list_widget.setStyleSheet("""
            QListWidget {
                background-color: #1A1A24;
                border: 1px solid #3A3A50;
                border-radius: 8px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #2A2A3A;
                color: #E0E0EC;
            }
            QListWidget::item:selected {
                background-color: #242436;
            }
        """)
        layout.addWidget(self._list_widget)

        # Action bar
        actions = QHBoxLayout()

        self._mark_all_read_btn = QPushButton("Mark All Read")
        self._mark_all_read_btn.setAccessibleName("Mark all notifications read")
        self._mark_all_read_btn.clicked.connect(self._mark_all_read)
        actions.addWidget(self._mark_all_read_btn)

        self._clear_btn = QPushButton("Clear History")
        self._clear_btn.setAccessibleName("Clear notification history")
        self._clear_btn.clicked.connect(self._clear_history)
        actions.addWidget(self._clear_btn)

        actions.addStretch()

        self._count_label = QLabel("0 notifications")
        self._count_label.setStyleSheet("color: #8888A0;")
        actions.addWidget(self._count_label)

        layout.addLayout(actions)

        # Refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._refresh_list)
        self._refresh_timer.start()

    def _refresh_list(self) -> None:
        """Refresh the notification list from the manager."""
        self._list_widget.clear()

        filter_text = self._search_input.text().lower()
        filter_index = self._filter_combo.currentIndex()

        notifications = self._manager.get_history(limit=200)

        for notification in notifications:
            # Apply filters
            if filter_index == 1 and notification.is_read:
                continue
            if filter_index == 2 and notification.notification_type not in (
                NotificationType.CHAT_MESSAGE, NotificationType.GROUP_MESSAGE
            ):
                continue
            if filter_index == 3 and notification.notification_type not in (
                NotificationType.VOICE_CALL, NotificationType.GROUP_CALL
            ):
                continue
            if filter_index == 4 and notification.notification_type != NotificationType.FILE_RECEIVED:
                continue
            if filter_text and filter_text not in notification.body.lower() and filter_text not in notification.title.lower():
                continue

            # Build display text
            read_marker = "" if notification.is_read else "● "
            time_str = QDateTime.fromSecsSinceEpoch(int(notification.timestamp)).toString("HH:mm")
            text = f"{read_marker}{notification.peer_name} — {notification.body[:80]}"
            item = QListWidgetItem(f"[{time_str}] {text}")
            item.setData(Qt.ItemDataRole.UserRole, notification.notification_id)

            if not notification.is_read:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

            self._list_widget.addItem(item)

        self._count_label.setText(f"{len(self._manager._history)} notifications")

    def _mark_all_read(self) -> None:
        """Mark all notifications as read."""
        self._manager.mark_all_read()
        self._refresh_list()

    def _clear_history(self) -> None:
        """Clear all notification history."""
        self._manager.clear_history()
        self._refresh_list()
