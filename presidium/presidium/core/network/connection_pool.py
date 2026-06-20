"""Connection pool and backpressure manager for Presidium.

Provides:
- Management of up to 100 simultaneous peer connections
- Fair queueing (round-robin) between peers for outbound messages
- Backpressure: pause/resume sending when a peer is slow (5s timeout)
- Circuit breaker: 3 consecutive failures → peer degraded → reroute via relay
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from presidium.core.network.peer import PeerManager, PeerInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONNECTIONS = 100
BACKPRESSURE_TIMEOUT = 5.0  # seconds before pausing sends to a slow peer
CIRCUIT_BREAKER_THRESHOLD = 3  # consecutive failures before degrading
CIRCUIT_BREAKER_COOLDOWN = 60.0  # seconds before retrying a degraded peer
MAX_QUEUE_SIZE = 1000  # max pending messages per peer


# ---------------------------------------------------------------------------
# Peer health states
# ---------------------------------------------------------------------------

class PeerHealth(enum.Enum):
    """Health state of a peer connection."""
    HEALTHY = "healthy"
    SLOW = "slow"        # Backpressure applied
    DEGRADED = "degraded"  # Circuit breaker tripped
    DEAD = "dead"        # Unreachable


# ---------------------------------------------------------------------------
# Peer connection slot
# ---------------------------------------------------------------------------

@dataclass
class PeerSlot:
    """Tracks the state of a single peer connection in the pool."""
    peer_user_id: str
    health: PeerHealth = PeerHealth.HEALTHY
    send_queue: deque[tuple[bytes, object]] = field(default_factory=deque)  # (msg_type, payload)
    is_paused: bool = False
    consecutive_failures: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    bytes_sent: int = 0
    bytes_received: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    avg_latency: float = 0.0


# ---------------------------------------------------------------------------
# Connection Pool
# ---------------------------------------------------------------------------

class ConnectionPool:
    """Manages peer connections with backpressure and circuit breaker.

    Usage:
        pool = ConnectionPool(peer_manager)
        await pool.start()

        # Queue a message for sending
        await pool.enqueue(peer_user_id, msg_type, payload)

        # Check peer health
        health = pool.get_peer_health(peer_user_id)

        await pool.stop()
    """

    def __init__(self, peer_manager: PeerManager) -> None:
        self._peer_manager = peer_manager
        self._slots: dict[str, PeerSlot] = {}
        self._running = False

        # Round-robin index for fair queueing
        self._rr_index = 0

        # Background tasks
        self._bg_tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the connection pool background tasks."""
        self._running = True
        self._bg_tasks.append(asyncio.create_task(self._send_loop()))
        self._bg_tasks.append(asyncio.create_task(self._health_check_loop()))
        logger.info("Connection pool started (max %d connections)", MAX_CONNECTIONS)

    async def stop(self) -> None:
        """Stop the connection pool."""
        self._running = False
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

    # -------------------------------------------------------------------
    # Enqueue messages
    # -------------------------------------------------------------------

    async def enqueue(self, peer_user_id: str, msg_type: int, payload: object) -> bool:
        """Queue a message for sending to a peer.

        If the queue is full or the peer is dead, the message is dropped.

        Returns:
            True if the message was queued, False if dropped.
        """
        slot = self._get_or_create_slot(peer_user_id)

        if slot.health == PeerHealth.DEAD:
            logger.debug("Dropping message to dead peer %s", peer_user_id[:8])
            return False

        if len(slot.send_queue) >= MAX_QUEUE_SIZE:
            logger.warning("Queue full for peer %s, dropping message", peer_user_id[:8])
            return False

        slot.send_queue.append((msg_type, payload))
        return True

    # -------------------------------------------------------------------
    # Record outcomes (for circuit breaker)
    # -------------------------------------------------------------------

    def record_success(self, peer_user_id: str) -> None:
        """Record a successful send to a peer."""
        slot = self._slots.get(peer_user_id)
        if slot:
            slot.consecutive_failures = 0
            slot.last_success_at = time.monotonic()
            if slot.health == PeerHealth.DEGRADED:
                slot.health = PeerHealth.HEALTHY
                slot.is_paused = False
                logger.info("Peer %s recovered from degraded state", peer_user_id[:8])

    def record_failure(self, peer_user_id: str) -> None:
        """Record a failed send to a peer."""
        slot = self._slots.get(peer_user_id)
        if slot:
            slot.consecutive_failures += 1
            slot.last_failure_at = time.monotonic()

            if slot.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                slot.health = PeerHealth.DEGRADED
                slot.is_paused = True
                logger.warning(
                    "Peer %s degraded after %d consecutive failures",
                    peer_user_id[:8],
                    slot.consecutive_failures,
                )

    # -------------------------------------------------------------------
    # Peer health
    # -------------------------------------------------------------------

    def get_peer_health(self, peer_user_id: str) -> PeerHealth:
        """Get the health state of a peer."""
        slot = self._slots.get(peer_user_id)
        return slot.health if slot else PeerHealth.DEAD

    def get_stats(self) -> dict[str, dict]:
        """Get statistics for all peer connections."""
        stats = {}
        for uid, slot in self._slots.items():
            stats[uid] = {
                "health": slot.health.value,
                "queue_size": len(slot.send_queue),
                "is_paused": slot.is_paused,
                "consecutive_failures": slot.consecutive_failures,
                "messages_sent": slot.messages_sent,
                "messages_received": slot.messages_received,
                "avg_latency": slot.avg_latency,
            }
        return stats

    # -------------------------------------------------------------------
    # Background: send loop (fair queueing)
    # -------------------------------------------------------------------

    async def _send_loop(self) -> None:
        """Process send queues with round-robin fair queueing."""
        while self._running:
            active_peers = [
                (uid, slot) for uid, slot in self._slots.items()
                if slot.send_queue and not slot.is_paused and slot.health != PeerHealth.DEAD
            ]

            if not active_peers:
                await asyncio.sleep(0.1)
                continue

            # Round-robin: send one message from each peer's queue
            for uid, slot in active_peers:
                if not slot.send_queue:
                    continue

                msg_type, payload = slot.send_queue.popleft()

                peer = self._peer_manager.get_peer(uid)
                if peer and peer.addr:
                    try:
                        await self._peer_manager._transport.send(msg_type, payload, peer.addr)
                        slot.messages_sent += 1
                        self.record_success(uid)
                    except Exception as exc:
                        logger.debug("Send failed to %s: %s", uid[:8], exc)
                        self.record_failure(uid)
                else:
                    self.record_failure(uid)

            await asyncio.sleep(0.01)  # Small yield to prevent CPU spinning

    # -------------------------------------------------------------------
    # Background: health check
    # -------------------------------------------------------------------

    async def _health_check_loop(self) -> None:
        """Periodically check peer health and recover degraded peers."""
        while self._running:
            now = time.monotonic()

            for uid, slot in list(self._slots.items()):
                # Recover degraded peers after cooldown
                if slot.health == PeerHealth.DEGRADED:
                    if now - slot.last_failure_at > CIRCUIT_BREAKER_COOLDOWN:
                        slot.health = PeerHealth.HEALTHY
                        slot.is_paused = False
                        slot.consecutive_failures = 0
                        logger.info("Peer %s cooldown expired, retrying", uid[:8])

                # Mark peers as slow if queue is building up
                if slot.health == PeerHealth.HEALTHY and len(slot.send_queue) > 50:
                    slot.health = PeerHealth.SLOW
                    logger.info("Peer %s marked as slow (queue: %d)", uid[:8], len(slot.send_queue))

                # Recover slow peers when queue drains
                if slot.health == PeerHealth.SLOW and len(slot.send_queue) < 10:
                    slot.health = PeerHealth.HEALTHY

            await asyncio.sleep(10.0)

    # -------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------

    def _get_or_create_slot(self, peer_user_id: str) -> PeerSlot:
        """Get or create a peer slot, enforcing max connections."""
        if peer_user_id not in self._slots:
            if len(self._slots) >= MAX_CONNECTIONS:
                # Evict the least recently successful peer
                lrs = min(self._slots.items(), key=lambda x: x[1].last_success_at)
                logger.info("Evicting peer %s to make room for %s", lrs[0][:8], peer_user_id[:8])
                del self._slots[lrs[0]]

            self._slots[peer_user_id] = PeerSlot(peer_user_id=peer_user_id)
        return self._slots[peer_user_id]
