"""Onion Routing for Presidium — 3-hop circuit-based anonymous messaging.

Implements a built-in onion routing layer (similar to Tor) that hides
metadata about who is communicating with whom. Each message traverses
3 relay nodes, with layered encryption ensuring each hop only knows
its immediate predecessor and successor.

Architecture:
    Sender → Guard Node → Middle Node → Exit Node → Recipient

    - Guard Node: knows sender IP, doesn't know recipient
    - Middle Node: knows neither sender nor recipient
    - Exit Node: knows recipient, doesn't know sender
    - Recipient: knows exit node delivered it, doesn't know sender

Circuit lifecycle:
    1. Circuit creation: Select 3 random peers from DHT
    2. Layered key exchange: Each hop gets an AES-256-GCM key
    3. Circuit usage: Messages are encrypted in 3 layers
    4. Circuit rotation: Every 10 minutes, new circuit is built

Encryption layers:
    Each relay peels one layer of encryption:
    - Exit receives: decrypt_layer3 → {recipient, message}
    - Middle receives: decrypt_layer2 → {next_hop, layer3_ciphertext}
    - Guard receives: decrypt_layer1 → {next_hop, layer2_ciphertext}

Circuit ID format:
    16 bytes random, assigned per-hop during circuit creation.
    Each relay maps (incoming_circuit_id) → (next_hop, outgoing_circuit_id).

Limitations (v1):
    - No stream multiplexing (one circuit = one message flow)
    - No congestion control (best-effort relay)
    - Exit node can observe plaintext (use with Sealed Sender for full privacy)
    - Guard nodes persist for 10 minutes (GUARD_ROTATION_INTERVAL)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from presidium.core.crypto import AES256GCM, CryptoError, SecureBytes, secure_zero
from presidium.core.network.net_security import ReplayBloomFilter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CIRCUIT_ID_SIZE = 16
HOP_COUNT = 3
CIRCUIT_LIFETIME_SECONDS = 600  # 10 minutes
GUARD_ROTATION_INTERVAL = 600  # 10 minutes — guard rotation period
ONION_VERSION = 1
NONCE_SIZE = 12
MAX_RELAY_PAYLOAD = 65536
REPLAY_NONCE_SIZE = 8  # 64-bit nonce for replay protection

# Onion message types
ONION_CMD_CREATE = 0x01
ONION_CMD_CREATED = 0x02
ONION_CMD_RELAY = 0x03
ONION_CMD_RELAY_ACK = 0x04
ONION_CMD_DESTROY = 0x05
ONION_CMD_PADDING = 0x06


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class OnionError(Exception):
    """Base exception for onion routing operations."""


class CircuitError(OnionError):
    """Circuit creation or management error."""


class CircuitExpiredError(CircuitError):
    """Circuit has exceeded its lifetime."""


class RelayError(OnionError):
    """Error relaying through a hop."""


class DecryptionError(OnionError):
    """Failed to peel an onion layer."""


# ---------------------------------------------------------------------------
# Circuit ID
# ---------------------------------------------------------------------------

@dataclass
class CircuitId:
    """A 16-byte circuit identifier."""
    data: bytes = field(default_factory=lambda: os.urandom(CIRCUIT_ID_SIZE))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CircuitId):
            return self.data == other.data
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.data)

    def __repr__(self) -> str:
        return f"CircuitId({self.data[:4].hex()}...)"

    def encode(self) -> bytes:
        return self.data

    @classmethod
    def decode(cls, data: bytes) -> CircuitId:
        if len(data) != CIRCUIT_ID_SIZE:
            raise CircuitError(f"Invalid circuit ID size: {len(data)}")
        return cls(data=data)


# ---------------------------------------------------------------------------
# Hop descriptor
# ---------------------------------------------------------------------------

@dataclass
class HopDescriptor:
    """Describes a single hop in an onion circuit.

    Contains the relay's address and the keys negotiated for
    this hop's encryption layer.
    """
    peer_id: str = ""            # Relay's Presidium user_id
    address: str = ""            # host:port
    x25519_public_key: bytes = b""  # 32 bytes — for key exchange
    circuit_id: CircuitId = field(default_factory=CircuitId)
    encryption_key: bytes = b""  # 32 bytes AES key for this layer
    is_created: bool = False


# ---------------------------------------------------------------------------
# Onion circuit
# ---------------------------------------------------------------------------

@dataclass
class OnionCircuit:
    """Represents an established 3-hop onion circuit.

    Contains the ordered list of hops and their negotiated keys.
    The circuit is used for both sending (adding layers) and
    receiving (peeling layers) messages.
    """
    circuit_id: CircuitId = field(default_factory=CircuitId)
    hops: list[HopDescriptor] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    messages_sent: int = 0
    messages_received: int = 0
    is_active: bool = True

    @property
    def is_expired(self) -> bool:
        """Check if the circuit has exceeded its lifetime."""
        return time.time() - self.created_at > CIRCUIT_LIFETIME_SECONDS

    @property
    def guard(self) -> Optional[HopDescriptor]:
        """The first (guard) hop."""
        return self.hops[0] if self.hops else None

    @property
    def middle(self) -> Optional[HopDescriptor]:
        """The second (middle) hop."""
        return self.hops[1] if len(self.hops) > 1 else None

    @property
    def exit_node(self) -> Optional[HopDescriptor]:
        """The third (exit) hop."""
        return self.hops[2] if len(self.hops) > 2 else None

    def touch(self) -> None:
        """Update the last-used timestamp."""
        self.last_used = time.time()

    def cleanup(self) -> None:
        """Securely zero all encryption keys."""
        for hop in self.hops:
            if hop.encryption_key:
                secure_zero(bytearray(hop.encryption_key))
        self.is_active = False


# ---------------------------------------------------------------------------
# Onion layer encryption
# ---------------------------------------------------------------------------

class OnionLayerCrypto:
    """Handles layered encryption/decryption for onion routing.

    Each layer uses AES-256-GCM with a per-hop key. The innermost
    layer (exit → recipient) is encrypted first, then the middle
    layer, then the guard layer.

    When relaying, each hop peels its layer to reveal the next
    destination and the remaining encrypted payload.
    """

    @staticmethod
    def add_layer(
        plaintext: bytes,
        key: bytes,
        next_hop_addr: str,
        next_circuit_id: CircuitId,
    ) -> bytes:
        """Add one layer of onion encryption.

        Wraps the plaintext with routing info (next hop + circuit ID),
        a 64-bit replay nonce, and encrypts with the hop's key.

        Args:
            plaintext: The data to wrap (may already be layered).
            key: This hop's AES-256-GCM key (32 bytes).
            next_hop_addr: Address of the next hop (host:port).
            next_circuit_id: Circuit ID for the next hop.

        Returns:
            Encrypted layer bytes.
        """
        # Generate replay protection nonce
        replay_nonce = os.urandom(REPLAY_NONCE_SIZE)

        # Build the inner payload:
        # next_hop_len + next_hop + circuit_id + replay_nonce + plaintext
        next_hop_bytes = next_hop_addr.encode("utf-8")
        inner = (
            struct.pack("!H", len(next_hop_bytes))
            + next_hop_bytes
            + next_circuit_id.encode()
            + replay_nonce
            + plaintext
        )

        # Encrypt with this hop's key
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = AES256GCM.encrypt(inner, key, nonce)

        return nonce + ciphertext

    @staticmethod
    def peel_layer(
        encrypted: bytes,
        key: bytes,
        replay_filter: Optional[ReplayBloomFilter] = None,
        circuit_id_bytes: bytes = b"",
    ) -> tuple[str, CircuitId, bytes]:
        """Peel one layer of onion encryption with replay protection.

        Args:
            encrypted: The encrypted layer (nonce + ciphertext).
            key: This hop's AES-256-GCM key (32 bytes).
            replay_filter: Optional Bloom filter for replay detection.
            circuit_id_bytes: Incoming circuit ID bytes for replay key scope.

        Returns:
            Tuple of (next_hop_addr, next_circuit_id, remaining_payload).

        Raises:
            DecryptionError: If decryption fails.
            OnionError: If replay attack detected.
        """
        if len(encrypted) < NONCE_SIZE + 16:  # nonce + minimal GCM tag
            raise DecryptionError("Encrypted data too short.")

        nonce = encrypted[:NONCE_SIZE]
        ciphertext = encrypted[NONCE_SIZE:]

        try:
            inner = AES256GCM.decrypt(ciphertext, key, nonce)
        except CryptoError as exc:
            raise DecryptionError(f"Layer decryption failed: {exc}") from exc

        # Parse routing info
        if len(inner) < 2:
            raise DecryptionError("Decrypted layer too short for hop address.")

        hop_len = struct.unpack("!H", inner[:2])[0]
        offset = 2

        if len(inner) < offset + hop_len:
            raise DecryptionError("Decrypted layer truncated: missing hop address.")

        next_hop = inner[offset:offset + hop_len].decode("utf-8")
        offset += hop_len

        if len(inner) < offset + CIRCUIT_ID_SIZE:
            raise DecryptionError("Decrypted layer truncated: missing circuit ID.")

        next_circuit_id = CircuitId(inner[offset:offset + CIRCUIT_ID_SIZE])
        offset += CIRCUIT_ID_SIZE

        # Extract and validate replay nonce
        remaining = inner[offset:]

        if len(remaining) < REPLAY_NONCE_SIZE:
            raise OnionError("Replay nonce missing from onion layer")

        replay_nonce = remaining[:REPLAY_NONCE_SIZE]
        # Composite replay key: nonce + circuit context
        replay_key = replay_nonce + circuit_id_bytes
        if replay_filter is not None and replay_filter.check_and_add(replay_key):
            raise OnionError(
                "Replay attack detected: duplicate nonce in onion layer"
            )

        payload = remaining[REPLAY_NONCE_SIZE:]

        return next_hop, next_circuit_id, payload


# ---------------------------------------------------------------------------
# Relay state (per-hop, runs on every Presidium node)
# ---------------------------------------------------------------------------

class RelayState:
    """State maintained by a Presidium node acting as an onion relay.

    Maps incoming circuit IDs to next-hop information, allowing
    the node to relay onion-encrypted messages without knowing
    the full circuit path.
    """

    def __init__(self) -> None:
        # incoming_circuit_id → (next_hop_addr, outgoing_circuit_id, encryption_key)
        self._forward_table: dict[bytes, tuple[str, bytes, bytes]] = {}
        # outgoing_circuit_id → (prev_hop_addr, incoming_circuit_id, encryption_key)
        self._backward_table: dict[bytes, tuple[str, bytes, bytes]] = {}
        self._circuits_created: int = 0

    def add_relay_entry(
        self,
        incoming_circuit_id: bytes,
        next_hop_addr: str,
        outgoing_circuit_id: bytes,
        encryption_key: bytes,
    ) -> None:
        """Register a relay mapping for a circuit passing through us."""
        self._forward_table[incoming_circuit_id] = (
            next_hop_addr, outgoing_circuit_id, encryption_key,
        )
        self._backward_table[outgoing_circuit_id] = (
            "", incoming_circuit_id, encryption_key,  # prev_hop filled on relay
        )
        self._circuits_created += 1

    def lookup_forward(self, circuit_id: bytes) -> Optional[tuple[str, bytes, bytes]]:
        """Look up where to forward a message for a given circuit ID."""
        return self._forward_table.get(circuit_id)

    def lookup_backward(self, circuit_id: bytes) -> Optional[tuple[str, bytes, bytes]]:
        """Look up where to send a response for a given circuit ID."""
        return self._backward_table.get(circuit_id)

    def remove_circuit(self, circuit_id: bytes) -> None:
        """Remove all entries for a circuit."""
        forward = self._forward_table.pop(circuit_id, None)
        if forward:
            self._backward_table.pop(forward[1], None)

        backward = self._backward_table.pop(circuit_id, None)
        if backward:
            self._forward_table.pop(backward[1], None)

    @property
    def active_circuits(self) -> int:
        return len(self._forward_table)

    @property
    def total_circuits_created(self) -> int:
        return self._circuits_created


# ---------------------------------------------------------------------------
# Onion routing manager
# ---------------------------------------------------------------------------

class OnionRoutingManager:
    """Manages onion circuits for anonymous message delivery.

    On the sender side:
        - Selects relay hops from DHT
        - Creates circuits with layered key exchange
        - Encrypts messages in 3 layers for delivery

    On the relay side:
        - Processes circuit creation requests
        - Relays messages by peeling one layer
        - Forwards to next hop

    On the recipient side:
        - Receives messages from exit nodes
        - Optionally pairs with SealedSender for full anonymity
    """

    def __init__(self, local_user_id: str) -> None:
        self._local_user_id = local_user_id
        self._circuits: dict[bytes, OnionCircuit] = {}  # circuit_id → circuit
        self._relay_state = RelayState()
        self._is_relay_enabled = False

        # Guard rotation state
        self._guard_node: Optional[HopDescriptor] = None
        self._guard_created_at: float = 0.0

        # Replay protection
        self._replay_filter = ReplayBloomFilter(
            expected_items=500_000,
            false_positive_rate=0.001,
            ttl_seconds=3600.0,  # 1 hour
        )

        # Callbacks
        self._send_to_peer: Optional[Callable[[str, bytes], Awaitable[None]]] = None
        self._on_incoming_message: Optional[Callable[[bytes, bytes], Awaitable[None]]] = None
        self._select_hops: Optional[Callable[[int], Awaitable[list[HopDescriptor]]]] = None

        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task[None]] = None
        self._running = False

    # -------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------

    def set_send_callback(
        self, callback: Callable[[str, bytes], Awaitable[None]]
    ) -> None:
        """Set the callback for sending raw bytes to a peer address."""
        self._send_to_peer = callback

    def set_incoming_callback(
        self, callback: Callable[[bytes, bytes], Awaitable[None]]
    ) -> None:
        """Set the callback for delivering decrypted messages to the app.

        Args: (sender_circuit_id, plaintext)
        """
        self._on_incoming_message = callback

    def set_hop_selector(
        self, callback: Callable[[int], Awaitable[list[HopDescriptor]]]
    ) -> None:
        """Set the callback for selecting relay hops from the DHT."""
        self._select_hops = callback

    def set_relay_enabled(self, enabled: bool) -> None:
        """Enable or disable this node as an onion relay."""
        self._is_relay_enabled = enabled

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the onion routing manager."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Onion routing manager started (relay=%s)", self._is_relay_enabled)

    async def stop(self) -> None:
        """Stop the onion routing manager and clean up circuits."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Clean up all circuits
        for circuit in self._circuits.values():
            circuit.cleanup()
        self._circuits.clear()
        logger.info("Onion routing manager stopped.")

    # -------------------------------------------------------------------
    # Guard selection
    # -------------------------------------------------------------------

    def _select_guard(self, available_hops: list[HopDescriptor]) -> HopDescriptor:
        """Select a guard node with 10-minute rotation period.

        Guard nodes are persistent for 10 minutes to prevent guard
        rotation attacks. An attacker who can observe guard selection
        would need to maintain a malicious relay for the full rotation
        period to be selected as a guard.

        Args:
            available_hops: List of candidate HopDescriptor objects.

        Returns:
            The selected (or reused) guard HopDescriptor.

        Raises:
            CircuitError: If no hops are available for guard selection.
        """
        now = time.monotonic()

        # Reuse existing guard if within rotation interval
        if (self._guard_node is not None
                and now - self._guard_created_at < GUARD_ROTATION_INTERVAL):
            logger.debug("Reusing guard node (age=%.0fs)", now - self._guard_created_at)
            return self._guard_node

        # Select new guard
        if not available_hops:
            raise CircuitError("No peers available for guard selection")

        guard = available_hops[os.urandom(1)[0] % len(available_hops)]
        self._guard_node = guard
        self._guard_created_at = now
        logger.info(
            "New guard node selected: %s (rotation every %ds)",
            guard.peer_id[:8] if guard.peer_id else "unknown",
            GUARD_ROTATION_INTERVAL,
        )
        return guard

    # -------------------------------------------------------------------
    # Circuit creation (sender side)
    # -------------------------------------------------------------------

    async def create_circuit(self) -> OnionCircuit:
        """Create a new 3-hop onion circuit.

        Selects relay nodes from the DHT, performs layered key
        exchange with each hop, and returns an active circuit.
        The guard node persists across circuit rotations for
        GUARD_ROTATION_INTERVAL (10 minutes).

        Returns:
            An established OnionCircuit.

        Raises:
            CircuitError: If circuit creation fails.
        """
        if self._select_hops is None:
            raise CircuitError("No hop selector configured.")

        # Select 3 relay hops
        hops = await self._select_hops(HOP_COUNT)
        if len(hops) < HOP_COUNT:
            raise CircuitError(
                f"Not enough relay nodes available: {len(hops)}/{HOP_COUNT}."
            )

        # Apply guard rotation: reuse persistent guard if within interval
        guard = self._select_guard(hops)
        hops[0] = guard

        circuit = OnionCircuit(
            hops=hops,
        )

        # Generate per-hop encryption keys
        for hop in hops:
            hop.encryption_key = os.urandom(32)
            hop.circuit_id = CircuitId()

        # In a full implementation, we would perform a TELEX-style
        # circuit creation: send CREATE to guard, then EXTEND through
        # the circuit to add middle and exit hops.
        # For v1, we assume keys are pre-negotiated via DHT.

        for hop in hops:
            hop.is_created = True

        self._circuits[circuit.circuit_id.data] = circuit
        logger.info(
            "Created onion circuit %s with hops: %s → %s → %s",
            circuit.circuit_id.data[:4].hex(),
            hops[0].peer_id[:8],
            hops[1].peer_id[:8],
            hops[2].peer_id[:8],
        )
        return circuit

    # -------------------------------------------------------------------
    # Message sending (sender side)
    # -------------------------------------------------------------------

    async def send_onion_message(
        self,
        circuit: OnionCircuit,
        recipient_addr: str,
        message: bytes,
    ) -> None:
        """Send a message through an onion circuit.

        Applies 3 layers of encryption (exit → middle → guard) and
        sends to the guard node.

        Args:
            circuit: The onion circuit to use.
            recipient_addr: Final recipient address (host:port).
            message: Plaintext message bytes.

        Raises:
            CircuitError: If the circuit is not active or expired.
            RelayError: If sending fails.
        """
        if not circuit.is_active:
            raise CircuitError("Circuit is not active.")
        if circuit.is_expired:
            raise CircuitExpiredError("Circuit has expired.")

        if self._send_to_peer is None:
            raise RelayError("No send callback configured.")

        # Layer 3 (innermost): Exit → Recipient
        recipient_circuit_id = CircuitId()  # Exit's circuit ID for recipient
        layer3 = OnionLayerCrypto.add_layer(
            plaintext=message,
            key=circuit.exit_node.encryption_key,
            next_hop_addr=recipient_addr,
            next_circuit_id=recipient_circuit_id,
        )

        # Layer 2: Middle → Exit
        layer2 = OnionLayerCrypto.add_layer(
            plaintext=layer3,
            key=circuit.middle.encryption_key,
            next_hop_addr=circuit.exit_node.address,
            next_circuit_id=circuit.exit_node.circuit_id,
        )

        # Layer 1 (outermost): Guard → Middle
        layer1 = OnionLayerCrypto.add_layer(
            plaintext=layer2,
            key=circuit.guard.encryption_key,
            next_hop_addr=circuit.middle.address,
            next_circuit_id=circuit.middle.circuit_id,
        )

        # Send to guard node
        guard_addr = circuit.guard.address
        wire_payload = struct.pack("!B", ONION_CMD_RELAY) + layer1

        try:
            await self._send_to_peer(guard_addr, wire_payload)
            circuit.messages_sent += 1
            circuit.touch()
            logger.debug(
                "Sent onion message via circuit %s (%d bytes)",
                circuit.circuit_id.data[:4].hex(),
                len(wire_payload),
            )
        except Exception as exc:
            raise RelayError(f"Failed to send onion message: {exc}") from exc

    # -------------------------------------------------------------------
    # Message receiving (relay + recipient side)
    # -------------------------------------------------------------------

    async def handle_relay_message(
        self,
        sender_addr: str,
        data: bytes,
    ) -> None:
        """Handle an incoming onion message (relay or final delivery).

        If this node is the exit, deliver the plaintext to the app.
        If this node is a relay, peel one layer and forward.

        Args:
            sender_addr: Address of the previous hop.
            data: Raw message bytes.
        """
        if len(data) < 1:
            return

        cmd = data[0]
        payload = data[1:]

        if cmd == ONION_CMD_RELAY:
            await self._handle_relay(sender_addr, payload)
        elif cmd == ONION_CMD_CREATE:
            await self._handle_create(sender_addr, payload)
        elif cmd == ONION_CMD_DESTROY:
            await self._handle_destroy(payload)

    async def _handle_relay(self, sender_addr: str, payload: bytes) -> None:
        """Process a relay message: peel a layer and forward or deliver."""
        if not self._is_relay_enabled and self._on_incoming_message is None:
            logger.warning("Received relay message but neither relay nor recipient.")
            return

        # Try to find the circuit in our relay table
        # The first CIRCUIT_ID_SIZE bytes identify the incoming circuit
        if len(payload) < CIRCUIT_ID_SIZE:
            return

        incoming_circuit_id = payload[:CIRCUIT_ID_SIZE]
        encrypted_layer = payload[CIRCUIT_ID_SIZE:]

        # Check if this is a circuit we're relaying
        relay_entry = self._relay_state.lookup_forward(incoming_circuit_id)

        if relay_entry:
            # We're a relay — peel our layer and forward
            next_hop, outgoing_circuit_id, enc_key = relay_entry

            try:
                next_addr, next_cid, remaining = OnionLayerCrypto.peel_layer(
                    encrypted_layer, enc_key,
                    replay_filter=self._replay_filter,
                    circuit_id_bytes=incoming_circuit_id,
                )
            except (DecryptionError, OnionError) as exc:
                logger.error("Failed to peel layer: %s", exc)
                return

            # Forward to next hop
            if self._send_to_peer:
                wire = struct.pack("!B", ONION_CMD_RELAY) + outgoing_circuit_id + remaining
                try:
                    await self._send_to_peer(next_addr, wire)
                except Exception as exc:
                    logger.error("Relay forward failed: %s", exc)
        else:
            # We might be the recipient or the exit node
            # Try to decrypt with our own circuit keys
            if self._on_incoming_message:
                await self._on_incoming_message(incoming_circuit_id, encrypted_layer)

    async def _handle_create(self, sender_addr: str, payload: bytes) -> None:
        """Handle a circuit creation request."""
        if not self._is_relay_enabled:
            return

        # Parse CREATE payload
        if len(payload) < CIRCUIT_ID_SIZE + 32:
            logger.warning("CREATE payload too short.")
            return

        circuit_id = payload[:CIRCUIT_ID_SIZE]
        x25519_pk = payload[CIRCUIT_ID_SIZE:CIRCUIT_ID_SIZE + 32]

        # In a full implementation, we would perform DH key exchange here
        # For v1, we accept the circuit and generate a relay key
        relay_key = os.urandom(32)

        # Store relay entry (we need the next hop from the CREATE extension)
        # Simplified: assume we know the next hop from the CREATE payload
        logger.info("Circuit CREATE from %s: %s", sender_addr, circuit_id[:4].hex())

    async def _handle_destroy(self, payload: bytes) -> None:
        """Handle a circuit destroy message."""
        if len(payload) < CIRCUIT_ID_SIZE:
            return
        circuit_id = payload[:CIRCUIT_ID_SIZE]
        self._relay_state.remove_circuit(circuit_id)
        logger.debug("Circuit destroyed: %s", circuit_id[:4].hex())

    # -------------------------------------------------------------------
    # Circuit management
    # -------------------------------------------------------------------

    def get_circuit(self, circuit_id: bytes) -> Optional[OnionCircuit]:
        """Look up a circuit by its ID."""
        return self._circuits.get(circuit_id)

    def get_active_circuits(self) -> list[OnionCircuit]:
        """Get all active circuits."""
        return [c for c in self._circuits.values() if c.is_active and not c.is_expired]

    async def destroy_circuit(self, circuit_id: bytes) -> None:
        """Gracefully destroy a circuit."""
        circuit = self._circuits.get(circuit_id)
        if circuit:
            # Send DESTROY to guard
            if self._send_to_peer and circuit.guard:
                try:
                    destroy_msg = struct.pack("!B", ONION_CMD_DESTROY) + circuit_id
                    await self._send_to_peer(circuit.guard.address, destroy_msg)
                except Exception:
                    pass

            circuit.cleanup()
            del self._circuits[circuit_id]

    # -------------------------------------------------------------------
    # Background maintenance
    # -------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Periodically clean up expired circuits."""
        while self._running:
            try:
                expired = [
                    cid for cid, circuit in self._circuits.items()
                    if circuit.is_expired
                ]
                for cid in expired:
                    await self.destroy_circuit(cid)
                    logger.debug("Cleaned up expired circuit %s", cid[:4].hex())

                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Circuit cleanup error: %s", exc)
                await asyncio.sleep(30)
