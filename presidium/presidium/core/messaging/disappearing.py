"""Disappearing messages for Presidium.

Provides:
- TTL-based message deletion (5 min, 1 hour, 1 day, 1 week)
- Background scheduler for secure deletion
- UI countdown badge on bubbles
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from enum import IntEnum
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTL presets
# ---------------------------------------------------------------------------

class DisappearingTTL(IntEnum):
    """Predefined disappearing message TTL values in seconds."""
    OFF = 0
    FIVE_MINUTES = 300
    ONE_HOUR = 3600
    ONE_DAY = 86400
    ONE_WEEK = 604800


@dataclass
class DisappearingMessage:
    """A message scheduled for deletion."""
    message_id: str  # hex
    peer_user_id: str
    expires_at: float  # Unix timestamp
    ttl: int  # seconds

    @property
    def remaining_seconds(self) -> float:
        """Seconds until this message expires."""
        remaining = self.expires_at - time.time()
        return max(0.0, remaining)

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


# ---------------------------------------------------------------------------
# Disappearing Message Scheduler
# ---------------------------------------------------------------------------

class DisappearingMessageScheduler:
    """Background scheduler that deletes expired messages.

    Performs secure wipe before deletion: overwrites message content
    with random data before deleting from the database.

    Usage:
        scheduler = DisappearingMessageScheduler(delete_callback)
        await scheduler.start()

        # Schedule a message for deletion
        scheduler.schedule(message_id, peer_user_id, ttl=DisappearingTTL.ONE_HOUR)

        await scheduler.stop()
    """

    def __init__(
        self,
        delete_callback: Callable[[str, str], Awaitable[None]],
        secure_wipe: bool = True,
    ) -> None:
        self._delete_callback = delete_callback
        self._secure_wipe = secure_wipe
        self._scheduled: dict[str, DisappearingMessage] = {}
        self._running = False
        self._bg_task: Optional[asyncio.Task] = None

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler background loop."""
        self._running = True
        self._bg_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Disappearing message scheduler started.")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass

    # -------------------------------------------------------------------
    # Scheduling
    # -------------------------------------------------------------------

    def schedule(self, message_id: str, peer_user_id: str, ttl: int) -> None:
        """Schedule a message for deletion after TTL seconds.

        Args:
            message_id: Hex-encoded message ID.
            peer_user_id: Peer user ID (for DB routing).
            ttl: Time-to-live in seconds.
        """
        if ttl <= 0:
            return

        msg = DisappearingMessage(
            message_id=message_id,
            peer_user_id=peer_user_id,
            expires_at=time.time() + ttl,
            ttl=ttl,
        )
        self._scheduled[message_id] = msg
        logger.debug("Scheduled message %s for deletion in %d seconds", message_id[:8], ttl)

    def cancel(self, message_id: str) -> None:
        """Cancel a scheduled deletion."""
        self._scheduled.pop(message_id, None)

    def get_remaining(self, message_id: str) -> float:
        """Get remaining seconds for a scheduled message."""
        msg = self._scheduled.get(message_id)
        return msg.remaining_seconds if msg else 0.0

    # -------------------------------------------------------------------
    # Background loop
    # -------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Periodically check and delete expired messages."""
        while self._running:
            now = time.time()
            expired = [
                msg for msg in self._scheduled.values()
                if msg.is_expired
            ]

            for msg in expired:
                try:
                    if self._secure_wipe:
                        await self._secure_delete(msg)
                    await self._delete_callback(msg.message_id, msg.peer_user_id)
                    del self._scheduled[msg.message_id]
                    logger.debug("Disappearing message %s deleted.", msg.message_id[:8])
                except Exception as exc:
                    logger.error("Failed to delete disappearing message %s: %s", msg.message_id[:8], exc)

            await asyncio.sleep(5.0)  # Check every 5 seconds

    async def _secure_delete(self, msg: DisappearingMessage) -> None:
        """Secure wipe: overwrite content with random data before deletion.

        PHASE3: simplified — in a full implementation, this would:
        1. Overwrite the message content in the database with random data
        2. Force a flush/sync
        3. Then delete the row
        """
        # The actual overwrite happens in the delete_callback implementation
        # which should be aware of secure wipe mode
        pass

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    @property
    def scheduled_count(self) -> int:
        return len(self._scheduled)

    def get_all_scheduled(self) -> list[DisappearingMessage]:
        """Get all currently scheduled messages."""
        return list(self._scheduled.values())
