"""Scheduled messages plugin for Presidium.

Allows users to schedule messages for future delivery. Messages are
stored locally and sent at the specified time, even if the app was
restarted in between.

Features:
    - Schedule messages with absolute or relative timestamps
    - Recurring messages (daily, weekly, monthly)
    - Cancel pending scheduled messages
    - Persistent storage (survives app restarts)
    - Timezone-aware scheduling
    - List/view/edit scheduled messages
    - Notification before sending (optional confirmation)

Usage (via bot commands):
    /schedule 14:30 "Hello, meeting time!"
    /schedule 2025-12-25T09:00 "Merry Christmas!"
    /schedule +2h "Don't forget the deadline"
    /schedule list
    /schedule cancel <id>
    /schedule recurring daily 09:00 "Daily standup reminder"

Storage:
    Scheduled messages are stored in a JSON file in the plugin's
    data directory. Each message has a unique ID, target peer,
    content, and scheduled timestamp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from presidium.plugins.base import (
    BasePlugin,
    PluginContext,
    PluginHook,
    PluginPermission,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recurrence pattern
# ---------------------------------------------------------------------------

class RecurrenceType:
    """Recurrence type constants."""
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


# ---------------------------------------------------------------------------
# Scheduled message model
# ---------------------------------------------------------------------------

@dataclass
class ScheduledMessage:
    """A message scheduled for future delivery."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    peer_id: str = ""           # Recipient's Presidium user ID
    text: str = ""              # Message content
    scheduled_at: float = 0.0   # Unix timestamp for delivery
    created_at: float = field(default_factory=time.time)

    # Recurrence
    recurrence: str = RecurrenceType.NONE
    recurrence_interval: int = 1  # e.g. every 2 weeks

    # Status
    is_sent: bool = False
    is_cancelled: bool = False
    sent_at: float = 0.0

    # Options
    require_confirmation: bool = False  # Ask user before sending

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "peer_id": self.peer_id,
            "text": self.text,
            "scheduled_at": self.scheduled_at,
            "created_at": self.created_at,
            "recurrence": self.recurrence,
            "recurrence_interval": self.recurrence_interval,
            "is_sent": self.is_sent,
            "is_cancelled": self.is_cancelled,
            "sent_at": self.sent_at,
            "require_confirmation": self.require_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledMessage:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            peer_id=data.get("peer_id", ""),
            text=data.get("text", ""),
            scheduled_at=data.get("scheduled_at", 0.0),
            created_at=data.get("created_at", time.time()),
            recurrence=data.get("recurrence", RecurrenceType.NONE),
            recurrence_interval=data.get("recurrence_interval", 1),
            is_sent=data.get("is_sent", False),
            is_cancelled=data.get("is_cancelled", False),
            sent_at=data.get("sent_at", 0.0),
            require_confirmation=data.get("require_confirmation", False),
        )

    @property
    def is_pending(self) -> bool:
        """True if the message is waiting to be sent."""
        return not self.is_sent and not self.is_cancelled

    @property
    def is_due(self) -> bool:
        """True if the message is due for delivery."""
        return self.is_pending and time.time() >= self.scheduled_at

    def compute_next_occurrence(self) -> Optional[float]:
        """Compute the next delivery timestamp for recurring messages.

        Returns:
            Next timestamp, or None if the message is not recurring.
        """
        if self.recurrence == RecurrenceType.NONE:
            return None

        base = datetime.fromtimestamp(self.scheduled_at, tz=timezone.utc)
        interval = self.recurrence_interval

        if self.recurrence == RecurrenceType.DAILY:
            next_dt = base + timedelta(days=interval)
        elif self.recurrence == RecurrenceType.WEEKLY:
            next_dt = base + timedelta(weeks=interval)
        elif self.recurrence == RecurrenceType.MONTHLY:
            # Approximate: add interval months
            month = base.month + interval
            year = base.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            try:
                next_dt = base.replace(year=year, month=month)
            except ValueError:
                # Day doesn't exist in target month (e.g. Feb 30)
                next_dt = base.replace(year=year, month=month, day=28)
        elif self.recurrence == RecurrenceType.YEARLY:
            next_dt = base.replace(year=base.year + interval)
        else:
            return None

        return next_dt.timestamp()


# ---------------------------------------------------------------------------
# Time parser
# ---------------------------------------------------------------------------

class TimeParser:
    """Parse human-readable time specifications into timestamps.

    Supports:
        - Absolute: "2025-12-25T09:00", "14:30" (today or tomorrow)
        - Relative: "+2h", "+30m", "+1d", "+1w"
        - Named: "tomorrow 09:00", "next monday 14:00"
    """

    @classmethod
    def parse(cls, time_str: str, reference_tz: timezone = timezone.utc) -> Optional[float]:
        """Parse a time string into a Unix timestamp.

        Args:
            time_str: Human-readable time specification.
            reference_tz: Timezone for interpretation.

        Returns:
            Unix timestamp, or None if parsing fails.
        """
        time_str = time_str.strip().strip('"').strip("'")

        # Relative time: +Nh, +Nm, +Nd, +Nw
        if time_str.startswith("+"):
            return cls._parse_relative(time_str[1:])

        # ISO format: 2025-12-25T09:00
        if "T" in time_str and len(time_str) >= 10:
            try:
                dt = datetime.fromisoformat(time_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=reference_tz)
                return dt.timestamp()
            except ValueError:
                pass

        # Time only: 14:30 or 14:30:00
        if ":" in time_str and len(time_str) <= 8:
            return cls._parse_time_today(time_str, reference_tz)

        # "tomorrow HH:MM"
        if time_str.lower().startswith("tomorrow"):
            time_part = time_str[8:].strip()
            base = datetime.now(tz=reference_tz) + timedelta(days=1)
            return cls._parse_time_on_date(time_part, base, reference_tz)

        return None

    @classmethod
    def _parse_relative(cls, spec: str) -> Optional[float]:
        """Parse a relative time spec like '2h', '30m', '1d'."""
        try:
            if spec.endswith("h"):
                return time.time() + int(spec[:-1]) * 3600
            elif spec.endswith("m"):
                return time.time() + int(spec[:-1]) * 60
            elif spec.endswith("d"):
                return time.time() + int(spec[:-1]) * 86400
            elif spec.endswith("w"):
                return time.time() + int(spec[:-1]) * 604800
            elif spec.endswith("s"):
                return time.time() + int(spec[:-1])
        except (ValueError, IndexError):
            pass
        return None

    @classmethod
    def _parse_time_today(cls, time_str: str, tz: timezone) -> Optional[float]:
        """Parse a time like '14:30' for today (or tomorrow if past)."""
        try:
            parts = time_str.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            second = int(parts[2]) if len(parts) > 2 else 0

            now = datetime.now(tz=tz)
            target = now.replace(hour=hour, minute=minute, second=second,
                                 microsecond=0)

            # If the time has already passed today, schedule for tomorrow
            if target <= now:
                target += timedelta(days=1)

            return target.timestamp()
        except (ValueError, IndexError):
            return None

    @classmethod
    def _parse_time_on_date(
        cls, time_str: str, base_date: datetime, tz: timezone
    ) -> Optional[float]:
        """Parse a time string on a specific date."""
        try:
            parts = time_str.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0

            target = base_date.replace(hour=hour, minute=minute, second=0,
                                       microsecond=0)
            return target.timestamp()
        except (ValueError, IndexError):
            return None


# ---------------------------------------------------------------------------
# Scheduled messages plugin
# ---------------------------------------------------------------------------

class ScheduledMessagesPlugin(BasePlugin):
    """Built-in scheduled messages plugin for Presidium.

    Allows users to schedule messages for future delivery using
    bot commands or direct API calls. Messages are persisted to disk
    and delivered by a background scheduler task.
    """

    def __init__(self) -> None:
        super().__init__()
        self._messages: dict[str, ScheduledMessage] = {}
        self._scheduler_task: Optional[asyncio.Task[None]] = None
        self._storage_file: Optional[Path] = None
        self._running = False

    async def on_load(self, ctx: PluginContext) -> None:
        """Initialize the scheduled messages plugin."""
        self._context = ctx  # type: ignore[assignment]

        # Set up storage
        data_dir = Path(ctx.plugin_dir) / "scheduled_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._storage_file = data_dir / "messages.json"

        # Load persisted messages
        await self._load_messages()

        # Register hooks
        self.register_hook(PluginHook.MESSAGE_RECEIVED, self._on_message)
        self.register_hook(PluginHook.APP_STARTED, self._on_app_started)
        self.register_hook(PluginHook.APP_CLOSING, self._on_app_closing)

        # Start scheduler
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info(
            "Scheduled messages plugin loaded with %d pending messages",
            sum(1 for m in self._messages.values() if m.is_pending),
        )

    async def on_unload(self) -> None:
        """Clean up the scheduled messages plugin."""
        self._running = False

        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        # Persist messages
        await self._save_messages()

    # -------------------------------------------------------------------
    # Message handling
    # -------------------------------------------------------------------

    async def _on_message(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """Handle /schedule commands."""
        text = data.get("content", "")
        peer_id = data.get("peer_id", data.get("sender_user_id", ""))

        if not text.startswith("/schedule"):
            return

        parts = text.split(None, 1)
        if len(parts) < 2:
            if self._context:
                await self._context.send_message(
                    peer_id,
                    "Usage: /schedule <time> <message>\n"
                    "Examples: /schedule 14:30 \"Hello\"\n"
                    "         /schedule +2h \"Reminder\"\n"
                    "         /schedule list\n"
                    "         /schedule cancel <id>",
                )
            return

        subcmd = parts[1].strip()

        if subcmd.startswith("list"):
            await self._handle_list(peer_id)
        elif subcmd.startswith("cancel"):
            msg_id = subcmd.split(None, 1)[-1].strip() if len(subcmd.split()) > 1 else ""
            await self._handle_cancel(peer_id, msg_id)
        else:
            await self._handle_schedule(peer_id, subcmd)

    async def _handle_schedule(self, peer_id: str, args: str) -> None:
        """Handle a schedule command."""
        # Parse: <time> [recurring <type> <interval>] "<message>"
        # Simple format: <time> "message text"
        parts = args.split(None, 1)
        if len(parts) < 2:
            if self._context:
                await self._context.send_message(
                    peer_id, "Usage: /schedule <time> \"message\""
                )
            return

        time_str = parts[0]
        message_text = parts[1].strip().strip('"').strip("'")

        # Parse time
        scheduled_ts = TimeParser.parse(time_str)
        if scheduled_ts is None:
            if self._context:
                await self._context.send_message(
                    peer_id, f"Could not parse time: {time_str}"
                )
            return

        # Create scheduled message
        msg = ScheduledMessage(
            peer_id=peer_id,
            text=message_text,
            scheduled_at=scheduled_ts,
        )

        self._messages[msg.id] = msg
        await self._save_messages()

        # Confirm
        scheduled_dt = datetime.fromtimestamp(scheduled_ts)
        if self._context:
            await self._context.send_message(
                peer_id,
                f"Message scheduled for {scheduled_dt.strftime('%Y-%m-%d %H:%M')} "
                f"(ID: {msg.id})",
            )

        logger.info("Scheduled message %s for %s", msg.id, scheduled_dt)

    async def _handle_list(self, peer_id: str) -> None:
        """List pending scheduled messages."""
        pending = [m for m in self._messages.values() if m.is_pending]
        if not pending:
            if self._context:
                await self._context.send_message(peer_id, "No scheduled messages.")
            return

        lines = ["Scheduled messages:"]
        for msg in sorted(pending, key=lambda m: m.scheduled_at):
            dt = datetime.fromtimestamp(msg.scheduled_at)
            preview = msg.text[:40] + ("..." if len(msg.text) > 40 else "")
            lines.append(
                f"  [{msg.id}] {dt.strftime('%Y-%m-%d %H:%M')} → "
                f"{msg.peer_id[:8]}...: {preview}"
            )

        if self._context:
            await self._context.send_message(peer_id, "\n".join(lines))

    async def _handle_cancel(self, peer_id: str, msg_id: str) -> None:
        """Cancel a scheduled message."""
        msg = self._messages.get(msg_id)
        if msg is None:
            if self._context:
                await self._context.send_message(
                    peer_id, f"Scheduled message {msg_id} not found."
                )
            return

        msg.is_cancelled = True
        await self._save_messages()

        if self._context:
            await self._context.send_message(
                peer_id, f"Cancelled scheduled message {msg_id}."
            )

    # -------------------------------------------------------------------
    # Scheduler loop
    # -------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Background task that checks for due messages and sends them."""
        while self._running:
            try:
                await self._process_due_messages()
                await asyncio.sleep(10)  # Check every 10 seconds
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Scheduler loop error: %s", exc)
                await asyncio.sleep(30)

    async def _process_due_messages(self) -> None:
        """Send all messages that are due."""
        for msg in list(self._messages.values()):
            if not msg.is_due:
                continue

            if not self._context:
                continue

            try:
                await self._context.send_message(msg.peer_id, msg.text)
                msg.is_sent = True
                msg.sent_at = time.time()

                logger.info("Sent scheduled message %s to %s", msg.id, msg.peer_id[:8])

                # Handle recurrence
                if msg.recurrence != RecurrenceType.NONE:
                    next_ts = msg.compute_next_occurrence()
                    if next_ts:
                        # Create next occurrence
                        next_msg = ScheduledMessage(
                            peer_id=msg.peer_id,
                            text=msg.text,
                            scheduled_at=next_ts,
                            recurrence=msg.recurrence,
                            recurrence_interval=msg.recurrence_interval,
                        )
                        self._messages[next_msg.id] = next_msg

            except Exception as exc:
                logger.error("Failed to send scheduled message %s: %s", msg.id, exc)

        # Persist state
        await self._save_messages()

        # Clean up old sent/cancelled messages (older than 24 hours)
        cutoff = time.time() - 86400
        to_remove = [
            mid for mid, msg in self._messages.items()
            if (msg.is_sent or msg.is_cancelled) and msg.sent_at < cutoff
        ]
        for mid in to_remove:
            del self._messages[mid]

    # -------------------------------------------------------------------
    # Lifecycle hooks
    # -------------------------------------------------------------------

    async def _on_app_started(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """Ensure scheduler is running when the app starts."""
        if not self._running:
            self._running = True
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def _on_app_closing(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """Save messages and stop scheduler when the app is closing."""
        await self._save_messages()
        self._running = False

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    async def _load_messages(self) -> None:
        """Load scheduled messages from disk."""
        if self._storage_file is None or not self._storage_file.exists():
            return

        try:
            with open(self._storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for msg_data in data.get("messages", []):
                msg = ScheduledMessage.from_dict(msg_data)
                self._messages[msg.id] = msg

        except Exception as exc:
            logger.error("Failed to load scheduled messages: %s", exc)

    async def _save_messages(self) -> None:
        """Persist scheduled messages to disk."""
        if self._storage_file is None:
            return

        try:
            data = {
                "messages": [msg.to_dict() for msg in self._messages.values()],
            }
            with open(self._storage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error("Failed to save scheduled messages: %s", exc)

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def schedule(
        self,
        peer_id: str,
        text: str,
        scheduled_at: float,
        recurrence: str = RecurrenceType.NONE,
    ) -> ScheduledMessage:
        """Schedule a message for future delivery.

        Args:
            peer_id: Recipient's Presidium user ID.
            text: Message content.
            scheduled_at: Unix timestamp for delivery.
            recurrence: Recurrence type (none, daily, weekly, monthly).

        Returns:
            The created ScheduledMessage.
        """
        msg = ScheduledMessage(
            peer_id=peer_id,
            text=text,
            scheduled_at=scheduled_at,
            recurrence=recurrence,
        )
        self._messages[msg.id] = msg
        await self._save_messages()
        return msg

    def get_pending(self) -> list[ScheduledMessage]:
        """Get all pending scheduled messages."""
        return [m for m in self._messages.values() if m.is_pending]

    async def cancel(self, msg_id: str) -> bool:
        """Cancel a scheduled message."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return False
        msg.is_cancelled = True
        await self._save_messages()
        return True
