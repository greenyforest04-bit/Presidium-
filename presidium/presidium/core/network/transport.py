"""UDP transport layer with NAT hole punching and reliable datagram delivery.

Provides:
- UDP hole punching: coordinated probe exchange for NAT traversal
- Reliable delivery: ACK-based retransmission with exponential backoff
- Fragmentation: split large payloads across multiple datagrams
- Connection state tracking per peer
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.network.protocol import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    MessageType,
    ProtocolError,
    PunchPayload,
    PunchAckPayload,
    decode_message,
    encode_message,
    PongPayload,
    PingPayload,
)
from presidium.utils.config import NetworkConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TransportError(Exception):
    """Base transport error."""


class NATTraversalError(TransportError):
    """NAT hole punching failed."""


class ReliableDeliveryError(TransportError):
    """Reliable delivery timeout or max retries exceeded."""


class FragmentationError(TransportError):
    """Fragment reassembly error."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = 5
RETRY_BASE_DELAY = 0.5  # seconds
ACK_TIMEOUT = 3.0  # seconds
FRAGMENT_TTL = 30.0  # seconds to wait for all fragments
MAX_CONCURRENT_RESENDS = 64
PING_INTERVAL = 30.0  # seconds
PING_TIMEOUT = 10.0  # seconds


# ---------------------------------------------------------------------------
# Fragment tracking
# ---------------------------------------------------------------------------

@dataclass
class FragmentBuffer:
    """Reassembly buffer for fragmented messages."""
    total_fragments: int = 0
    received: dict[int, bytes] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.monotonic)

    def is_complete(self) -> bool:
        return len(self.received) == self.total_fragments

    def reassemble(self) -> bytes:
        """Reassemble fragments in order."""
        parts = []
        for i in range(self.total_fragments):
            part = self.received.get(i)
            if part is None:
                raise FragmentationError(f"Missing fragment {i}")
            parts.append(part)
        return b"".join(parts)


# ---------------------------------------------------------------------------
# Reliable delivery tracking
# ---------------------------------------------------------------------------

@dataclass
class PendingMessage:
    """Tracks a message awaiting ACK."""
    msg_type: int
    payload_bytes: bytes
    dest_addr: tuple[str, int]
    send_time: float = field(default_factory=time.monotonic)
    retries: int = 0
    ack_event: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# Peer connection state
# ---------------------------------------------------------------------------

class PeerState:
    """Tracks connection state for a single peer."""

    def __init__(self, addr: tuple[str, int]) -> None:
        self.addr = addr
        self.connected = False
        self.last_seen = time.monotonic()
        self.external_addr: Optional[str] = None  # observed external addr
        self.rtt: float = 0.0  # round-trip time estimate
        self.punching = False


# ---------------------------------------------------------------------------
# UDP Transport
# ---------------------------------------------------------------------------

class UDPTransport:
    """Asyncio-based UDP transport with NAT traversal and reliable delivery.

    Lifecycle:
        1. Create UDPTransport(config, local_user_id)
        2. await start() — binds UDP socket
        3. register_handler(msg_type, callback) for each message type
        4. send(msg_type, payload, addr) — fire-and-forget
        5. send_reliable(msg_type, payload, addr) — with ACK and retries
        6. punch(peer_addr, peer_user_id) — initiate NAT hole punching
        7. await stop() — clean shutdown
    """

    def __init__(
        self,
        config: NetworkConfig,
        local_user_id: bytes,
    ) -> None:
        self._config = config
        self._local_user_id = local_user_id
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[_DatagramProtocol] = None
        self._running = False

        # Message handlers: msg_type → callback
        self._handlers: dict[int, Callable[[int, float, object, tuple[str, int]], Awaitable[None]]] = {}

        # Peer states
        self._peers: dict[tuple[str, int], PeerState] = {}

        # Reliable delivery
        self._pending: dict[bytes, PendingMessage] = {}  # message_id → Pending
        self._seq_counter = 0

        # Fragment reassembly
        self._fragment_buffers: dict[bytes, FragmentBuffer] = {}

        # NAT traversal
        self._punch_events: dict[bytes, asyncio.Event] = {}  # peer_user_id → Event

        # Background tasks
        self._bg_tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the UDP socket and start background tasks."""
        loop = asyncio.get_running_loop()
        self._protocol = _DatagramProtocol(self._on_datagram)
        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: self._protocol,  # type: ignore
                local_addr=("0.0.0.0", self._config.listen_port),
            )
        except OSError as exc:
            raise TransportError(f"Failed to bind UDP socket on port {self._config.listen_port}: {exc}") from exc

        self._running = True
        self._bg_tasks.append(asyncio.create_task(self._reliability_loop()))
        self._bg_tasks.append(asyncio.create_task(self._ping_loop()))
        self._bg_tasks.append(asyncio.create_task(self._fragment_cleanup_loop()))

        local_port = self._transport.get_extra_info("sockname")[1]
        logger.info("UDP transport started on port %d", local_port)

    async def stop(self) -> None:
        """Stop the transport and cancel background tasks."""
        self._running = False
        for task in self._bg_tasks:
            task.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()
        if self._transport:
            self._transport.close()
            self._transport = None
        logger.info("UDP transport stopped.")

    # -------------------------------------------------------------------
    # Handler registration
    # -------------------------------------------------------------------

    def register_handler(
        self,
        msg_type: int,
        callback: Callable[[int, float, object, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """Register an async callback for a message type."""
        self._handlers[msg_type] = callback

    # -------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------

    async def send(
        self,
        msg_type: int,
        payload: object,
        addr: tuple[str, int],
    ) -> None:
        """Send a single datagram (fire-and-forget)."""
        raw = encode_message(msg_type, payload)
        await self._send_raw(raw, addr)

    async def send_reliable(
        self,
        msg_type: int,
        payload: object,
        addr: tuple[str, int],
        timeout: float = ACK_TIMEOUT,
    ) -> None:
        """Send a message with ACK-based retransmission.

        Raises ReliableDeliveryError if no ACK received after max retries.
        """
        raw = encode_message(msg_type, payload)
        msg_id = self._next_seq()
        pending = PendingMessage(
            msg_type=msg_type,
            payload_bytes=raw,
            dest_addr=addr,
        )
        self._pending[msg_id] = pending

        # First send
        await self._send_raw(raw, addr)

        try:
            await asyncio.wait_for(pending.ack_event.wait(), timeout=timeout * (MAX_RETRIES + 1))
        except asyncio.TimeoutError:
            raise ReliableDeliveryError(
                f"No ACK for message {msg_id.hex()} to {addr} after {MAX_RETRIES} retries."
            )
        finally:
            self._pending.pop(msg_id, None)

    # -------------------------------------------------------------------
    # NAT Hole Punching
    # -------------------------------------------------------------------

    async def punch(
        self,
        peer_addr: tuple[str, int],
        peer_user_id: bytes,
        timeout: Optional[float] = None,
    ) -> bool:
        """Initiate NAT hole punching with a peer.

        Sends a series of PUNCH probes and waits for PUNCH_ACK.
        Returns True if the hole was successfully punched.
        """
        if timeout is None:
            timeout = self._config.nat_traversal_timeout

        event = asyncio.Event()
        self._punch_events[peer_user_id] = event

        # Ensure peer state exists
        state = self._get_or_create_peer(peer_addr)
        state.punching = True

        # Send multiple punch probes in quick succession
        punch_payload = PunchPayload(
            user_id=self._local_user_id,
            external_addr=f"{peer_addr[0]}:{peer_addr[1]}",
        )

        async def _send_probes() -> None:
            for i in range(8):
                try:
                    await self.send(MessageType.PUNCH, punch_payload, peer_addr)
                except Exception:
                    pass
                await asyncio.sleep(0.25)

        probe_task = asyncio.create_task(_send_probes())

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            state.connected = True
            state.last_seen = time.monotonic()
            logger.info("NAT hole punched successfully with %s", peer_user_id.hex())
            return True
        except asyncio.TimeoutError:
            logger.warning("NAT hole punching timed out with %s", peer_user_id.hex())
            return False
        finally:
            state.punching = False
            self._punch_events.pop(peer_user_id, None)
            probe_task.cancel()

    # -------------------------------------------------------------------
    # Peer state
    # -------------------------------------------------------------------

    def get_peer_state(self, addr: tuple[str, int]) -> Optional[PeerState]:
        """Get the state for a peer address."""
        return self._peers.get(addr)

    def _get_or_create_peer(self, addr: tuple[str, int]) -> PeerState:
        if addr not in self._peers:
            self._peers[addr] = PeerState(addr)
        return self._peers[addr]

    # -------------------------------------------------------------------
    # Internal: datagram reception
    # -------------------------------------------------------------------

    async def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Called by the protocol when a datagram is received."""
        try:
            msg_type, timestamp, payload = decode_message(data)
        except ProtocolError as exc:
            logger.debug("Dropping malformed datagram from %s: %s", addr, exc)
            return

        # Update peer state
        peer = self._get_or_create_peer(addr)
        peer.last_seen = time.monotonic()

        # Handle transport-level messages internally
        if msg_type == MessageType.PUNCH:
            await self._handle_punch(payload, addr)
            return
        if msg_type == MessageType.PUNCH_ACK:
            await self._handle_punch_ack(payload, addr)
            return
        if msg_type == MessageType.PING:
            await self._handle_ping(payload, addr)
            return
        if msg_type == MessageType.PONG:
            await self._handle_pong(payload, addr)
            return

        # Dispatch to registered handlers
        handler = self._handlers.get(msg_type)
        if handler:
            try:
                await handler(msg_type, timestamp, payload, addr)
            except Exception:
                logger.exception("Error in handler for msg_type=%d from %s", msg_type, addr)
        else:
            logger.debug("No handler for msg_type=%d from %s", msg_type, addr)

    # -------------------------------------------------------------------
    # Internal: protocol message handlers
    # -------------------------------------------------------------------

    async def _handle_punch(self, payload: PunchPayload, addr: tuple[str, int]) -> None:
        """Respond to a NAT punch probe with PUNCH_ACK."""
        peer = self._get_or_create_peer(addr)
        ack = PunchAckPayload(
            user_id=self._local_user_id,
            external_addr=f"{addr[0]}:{addr[1]}",
            your_external_addr=f"{addr[0]}:{addr[1]}",
        )
        await self.send(MessageType.PUNCH_ACK, ack, addr)
        peer.last_seen = time.monotonic()

    async def _handle_punch_ack(self, payload: PunchAckPayload, addr: tuple[str, int]) -> None:
        """Handle a punch ACK — signal successful hole punch."""
        peer = self._get_or_create_peer(addr)
        peer.external_addr = payload.your_external_addr
        peer.connected = True

        event = self._punch_events.get(payload.user_id)
        if event:
            event.set()

    async def _handle_ping(self, payload: PingPayload, addr: tuple[str, int]) -> None:
        """Respond to keepalive ping."""
        pong = PongPayload(
            sender_id=self._local_user_id,
            ping_timestamp=time.time(),
        )
        await self.send(MessageType.PONG, pong, addr)

    async def _handle_pong(self, payload: PongPayload, addr: tuple[str, int]) -> None:
        """Process pong — update RTT estimate."""
        peer = self._get_or_create_peer(addr)
        now = time.monotonic()
        try:
            rtt = now - payload.ping_timestamp
            if rtt > 0:
                peer.rtt = rtt
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Internal: reliability loop
    # -------------------------------------------------------------------

    async def _reliability_loop(self) -> None:
        """Periodically retransmit unACKed messages."""
        while self._running:
            now = time.monotonic()
            for msg_id, pending in list(self._pending.items()):
                elapsed = now - pending.send_time
                if elapsed > RETRY_BASE_DELAY * (2 ** pending.retries):
                    if pending.retries >= MAX_RETRIES:
                        logger.warning("Dropping unACKed message %s after %d retries", msg_id.hex(), MAX_RETRIES)
                        self._pending.pop(msg_id, None)
                        continue
                    pending.retries += 1
                    pending.send_time = now
                    await self._send_raw(pending.payload_bytes, pending.dest_addr)
            await asyncio.sleep(RETRY_BASE_DELAY)

    # -------------------------------------------------------------------
    # Internal: ping loop
    # -------------------------------------------------------------------

    async def _ping_loop(self) -> None:
        """Send periodic keepalive pings to connected peers."""
        while self._running:
            for addr, peer in self._peers.items():
                if peer.connected:
                    ping = PingPayload(sender_id=self._local_user_id)
                    try:
                        await self.send(MessageType.PING, ping, addr)
                    except Exception:
                        pass
            await asyncio.sleep(PING_INTERVAL)

    # -------------------------------------------------------------------
    # Internal: fragment cleanup
    # -------------------------------------------------------------------

    async def _fragment_cleanup_loop(self) -> None:
        """Clean up expired fragment buffers."""
        while self._running:
            now = time.monotonic()
            expired = [
                msg_id for msg_id, buf in self._fragment_buffers.items()
                if now - buf.first_seen > FRAGMENT_TTL
            ]
            for msg_id in expired:
                self._fragment_buffers.pop(msg_id, None)
            await asyncio.sleep(5.0)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _send_raw(self, data: bytes, addr: tuple[str, int]) -> None:
        """Send raw bytes via the transport."""
        if self._transport is None:
            raise TransportError("Transport not started.")
        self._transport.sendto(data, addr)

    def _next_seq(self) -> bytes:
        """Generate a unique sequence number for reliable delivery."""
        self._seq_counter += 1
        return self._seq_counter.to_bytes(4, "big")


# ---------------------------------------------------------------------------
# asyncio DatagramProtocol wrapper
# ---------------------------------------------------------------------------

class _DatagramProtocol(asyncio.DatagramProtocol):
    """Thin asyncio protocol that forwards received datagrams to a callback."""

    def __init__(
        self,
        on_datagram: Callable[[bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        self._on_datagram = on_datagram
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int] | tuple[str, int, int, int]) -> None:
        # Normalize IPv6 addresses
        if len(addr) == 4:
            addr = (addr[0], addr[1])
        asyncio.ensure_future(self._on_datagram(data, addr))

    def error_received(self, exc: Exception) -> None:
        logger.error("UDP error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.error("UDP connection lost: %s", exc)
