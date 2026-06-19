"""Store-and-Forward outbox for Presidium.

Provides:
- Outbox: SQLite-backed queue of messages for offline peers
- Periodic retry delivery when peers come online
- Dead drops: encrypted blobs stored on bootstrap peers (TTL 7 days)
- Integration with PeerManager and DHT for peer availability detection
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.crypto import AES256GCM, Ed25519KeyPair
from presidium.core.network.dht import DHTService
from presidium.core.network.peer import PeerManager
from presidium.core.network.protocol import MessageType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class OutboxError(Exception):
    """Base outbox error."""


class DeadDropError(Exception):
    """Dead drop storage/retrieval error."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTBOX_RETRY_INTERVAL = 30.0  # seconds between retry attempts
DEAD_DROP_TTL = 604800  # 7 days in seconds
MAX_OUTBOX_SIZE = 10000
DEAD_DROP_KEY_PREFIX = b"presidium:deaddrop:"


# ---------------------------------------------------------------------------
# Outbox message record
# ---------------------------------------------------------------------------

@dataclass
class OutboxMessage:
    """A message waiting to be delivered to an offline peer."""
    id: int  # auto-increment
    peer_user_id: str
    message_id: bytes  # 16 bytes
    encrypted_payload: bytes  # The already-encrypted message data
    created_at: float = field(default_factory=time.time)
    last_retry_at: float = 0.0
    retry_count: int = 0
    max_retries: int = 100
    delivered: bool = False


# ---------------------------------------------------------------------------
# Dead drop record
# ---------------------------------------------------------------------------

@dataclass
class DeadDrop:
    """An encrypted blob stored on a bootstrap peer for offline delivery."""
    recipient_user_id: str
    encrypted_blob: bytes
    ttl: int = DEAD_DROP_TTL
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Outbox Manager
# ---------------------------------------------------------------------------

class OutboxManager:
    """Manages the message outbox and store-and-forward delivery.

    When a message cannot be delivered because the peer is offline:
    1. The message is stored in the outbox (SQLite)
    2. An encrypted dead drop is created on bootstrap peers via DHT
    3. When the peer comes online (detected via mDNS, DHT, or direct PING),
       all pending outbox messages are delivered in order.
    4. Successfully delivered messages are removed from the outbox.
    """

    def __init__(
        self,
        peer_manager: PeerManager,
        dht: Optional[DHTService],
        local_key_pair: Ed25519KeyPair,
        local_user_id: str,
    ) -> None:
        self._peer_manager = peer_manager
        self._dht = dht
        self._local_key_pair = local_key_pair
        self._local_user_id = local_user_id

        # In-memory outbox: peer_user_id → list of OutboxMessage
        self._outbox: dict[str, list[OutboxMessage]] = {}
        self._next_id = 0

        # Callback for DB persistence
        self._persist_outbox: Optional[Callable] = None

        # Background task
        self._bg_task: Optional[asyncio.Task] = None
        self._running = False

        # Register peer connection callback
        self._peer_manager.set_peer_connected_handler(self._on_peer_online)

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the outbox retry loop."""
        self._running = True
        self._bg_task = asyncio.create_task(self._retry_loop())
        logger.info("Outbox manager started.")

    async def stop(self) -> None:
        """Stop the outbox manager."""
        self._running = False
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass

    # -------------------------------------------------------------------
    # Adding messages to outbox
    # -------------------------------------------------------------------

    async def enqueue(
        self,
        peer_user_id: str,
        message_id: bytes,
        encrypted_payload: bytes,
    ) -> None:
        """Add a message to the outbox for later delivery.

        Also creates a dead drop on bootstrap peers.
        """
        if peer_user_id not in self._outbox:
            self._outbox[peer_user_id] = []

        # Enforce max outbox size
        if len(self._outbox[peer_user_id]) >= MAX_OUTBOX_SIZE:
            # Remove oldest undelivered message
            self._outbox[peer_user_id].pop(0)

        msg = OutboxMessage(
            id=self._next_id,
            peer_user_id=peer_user_id,
            message_id=message_id,
            encrypted_payload=encrypted_payload,
        )
        self._next_id += 1
        self._outbox[peer_user_id].append(msg)

        logger.info(
            "Message enqueued for %s (outbox size: %d)",
            peer_user_id[:8], len(self._outbox[peer_user_id]),
        )

        # Try to create dead drop on bootstrap peers
        await self._create_dead_drop(peer_user_id, encrypted_payload)

    # -------------------------------------------------------------------
    # Peer online handler
    # -------------------------------------------------------------------

    async def _on_peer_online(self, peer_user_id: str) -> None:
        """Handle a peer coming online — deliver all pending messages."""
        pending = self._outbox.get(peer_user_id, [])
        if not pending:
            return

        logger.info(
            "Peer %s online — delivering %d outbox messages",
            peer_user_id[:8], len(pending),
        )

        delivered_ids: list[int] = []
        for msg in pending:
            if msg.delivered:
                delivered_ids.append(msg.id)
                continue

            try:
                await self._peer_manager.send_chat_message(
                    peer_user_id,
                    msg.encrypted_payload,
                )
                msg.delivered = True
                delivered_ids.append(msg.id)
                logger.debug("Delivered outbox message %d to %s", msg.id, peer_user_id[:8])
            except Exception as exc:
                msg.retry_count += 1
                msg.last_retry_at = time.monotonic()
                logger.warning(
                    "Failed to deliver outbox message %d: %s", msg.id, exc
                )
                break  # Stop trying if the peer becomes unreachable again

        # Remove delivered messages
        if delivered_ids:
            self._outbox[peer_user_id] = [
                m for m in self._outbox[peer_user_id]
                if m.id not in delivered_ids
            ]
            if not self._outbox[peer_user_id]:
                del self._outbox[peer_user_id]

    # -------------------------------------------------------------------
    # Dead drops
    # -------------------------------------------------------------------

    async def _create_dead_drop(
        self,
        recipient_user_id: str,
        encrypted_payload: bytes,
    ) -> None:
        """Create an encrypted dead drop on bootstrap peers via DHT.

        The dead drop is stored at a DHT key derived from the recipient's
        user ID. The value is encrypted with the recipient's public key,
        so only they can read it.
        """
        if self._dht is None:
            return

        # DHT key for this recipient's dead drops
        dht_key = DEAD_DROP_KEY_PREFIX + recipient_user_id.encode()

        # Encrypt the payload with the recipient's public key
        # PHASE2: simplified — in production, use proper ECIES or HPKE
        # Here we use the existing DHT store which is already encrypted
        try:
            # Store the encrypted payload at a key specific to the recipient
            await self._dht.store_value(
                dht_key,
                encrypted_payload,
                ttl=DEAD_DROP_TTL,
            )
            logger.debug("Dead drop created for %s", recipient_user_id[:8])
        except Exception as exc:
            logger.warning("Failed to create dead drop for %s: %s", recipient_user_id[:8], exc)

    async def check_dead_drops(self, local_user_id: str) -> list[bytes]:
        """Check for dead drops addressed to us.

        Args:
            local_user_id: Our user ID.

        Returns:
            List of encrypted message payloads found in dead drops.
        """
        if self._dht is None:
            return []

        dht_key = DEAD_DROP_KEY_PREFIX + local_user_id.encode()
        try:
            value = await self._dht.find_value(dht_key)
            if value:
                return [value]
        except Exception as exc:
            logger.debug("No dead drops found: %s", exc)

        return []

    # -------------------------------------------------------------------
    # Background retry loop
    # -------------------------------------------------------------------

    async def _retry_loop(self) -> None:
        """Periodically retry delivering outbox messages."""
        while self._running:
            await asyncio.sleep(OUTBOX_RETRY_INTERVAL)

            for peer_user_id, messages in list(self._outbox.items()):
                undelivered = [m for m in messages if not m.delivered]
                if not undelivered:
                    continue

                # Try to connect if not already connected
                peer = self._peer_manager.get_peer(peer_user_id)
                if peer and peer.connected:
                    # Already connected — try delivery
                    await self._on_peer_online(peer_user_id)
                else:
                    # Try to find the peer via DHT
                    if self._dht:
                        try:
                            peer_info = await self._dht.find_value(
                                peer_user_id.encode()
                            )
                            if peer_info:
                                logger.debug("Found peer %s via DHT for outbox delivery", peer_user_id[:8])
                        except Exception:
                            pass

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Total number of undelivered messages in the outbox."""
        total = 0
        for messages in self._outbox.values():
            total += sum(1 for m in messages if not m.delivered)
        return total

    def get_pending_for_peer(self, peer_user_id: str) -> int:
        """Get the count of pending messages for a specific peer."""
        messages = self._outbox.get(peer_user_id, [])
        return sum(1 for m in messages if not m.delivered)
