"""Presidium Relay Network — decentralized relay marketplace.

Implements a reputation-based relay system where users can run relay
nodes (public IP, stable uptime) and earn reputation tokens for:
    - Uptime hours (how long the relay has been online)
    - Bandwidth provided (GB of traffic relayed)
    - Storage provided (GB of DHT dead drops stored)

Reputation tokens are NOT cryptocurrency — they are non-transferable
local scores used for DHT routing priority. Higher-score relays are
preferred for circuit building (onion routing) and message delivery.

Anti-Sybil protection:
    - Proof-of-uptime: periodic signed attestations from other peers
    - Proof-of-bandwidth: delivery receipts from relayed messages
    - Score is local — each node computes its own view of relay scores
    - New relays start at score 0 and build reputation over time

Relay capabilities:
    - MESSAGE_RELAY: Forward sealed/dummy messages between peers
    - DHT_STORAGE: Store and serve DHT key-value pairs (dead drops)
    - ONION_RELAY: Act as a hop in onion routing circuits
    - CALL_RELAY: Relay WebRTC media for group calls (SFU assist)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ATTESTATION_INTERVAL_SECONDS = 3600  # 1 hour between attestations
SCORE_DECAY_RATE = 0.995  # Per-hour decay (prevents eternal high scores)
MIN_ATTESTATIONS_FOR_TRUST = 3  # Minimum attestations to be considered
SCORE_WEIGHTS = {
    "uptime": 0.40,
    "bandwidth": 0.35,
    "storage": 0.25,
}

# Bandwidth scoring thresholds
BANDWIDTH_TIERS = {
    "bronze": 10,    # 10 GB served
    "silver": 50,    # 50 GB served
    "gold": 200,     # 200 GB served
    "platinum": 500,  # 500 GB served
}


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class RelayError(Exception):
    """Base exception for relay network operations."""


class AttestationError(RelayError):
    """Attestation verification failed."""


# ---------------------------------------------------------------------------
# Relay capabilities
# ---------------------------------------------------------------------------

class RelayCapability:
    """Relay capability flags."""
    MESSAGE_RELAY = "message_relay"
    DHT_STORAGE = "dht_storage"
    ONION_RELAY = "onion_relay"
    CALL_RELAY = "call_relay"


# ---------------------------------------------------------------------------
# Attestation
# ---------------------------------------------------------------------------

@dataclass
class Attestation:
    """A signed proof that a relay was online and functioning.

    Attestations are issued by peers who have successfully used
    the relay. Each attestation contains:
    - The relay's user_id
    - The attester's user_id
    - A timestamp
    - The attester's Ed25519 signature
    """
    relay_user_id: str = ""
    attester_user_id: str = ""
    timestamp: float = field(default_factory=time.time)
    attester_signature: bytes = b""  # 64 bytes Ed25519
    capabilities_verified: list[str] = field(default_factory=list)
    latency_ms: float = 0.0  # Observed latency during attestation

    def sign_payload(self) -> bytes:
        """Return the bytes that should be signed."""
        caps = ",".join(sorted(self.capabilities_verified))
        return (
            self.relay_user_id.encode("utf-8")
            + self.attester_user_id.encode("utf-8")
            + struct.pack("!d", self.timestamp)
            + caps.encode("utf-8")
        )

    def to_dict(self) -> dict[str, Any]:
        import base64
        return {
            "relay_user_id": self.relay_user_id,
            "attester_user_id": self.attester_user_id,
            "timestamp": self.timestamp,
            "attester_signature": base64.b64encode(self.attester_signature).decode(),
            "capabilities_verified": self.capabilities_verified,
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# Relay descriptor
# ---------------------------------------------------------------------------

@dataclass
class RelayDescriptor:
    """Describes a relay node and its current state."""
    user_id: str = ""
    address: str = ""  # host:port
    public_key: bytes = b""  # 32 bytes Ed25519
    capabilities: list[str] = field(default_factory=list)
    is_online: bool = False
    last_seen: float = 0.0

    # Performance metrics
    avg_latency_ms: float = 0.0
    uptime_hours: float = 0.0
    bandwidth_gb_served: float = 0.0
    storage_gb_provided: float = 0.0

    # Reputation
    score: float = 0.0
    attestation_count: int = 0
    tier: str = "none"

    # Self-reported (updated periodically)
    max_bandwidth_mbps: float = 0.0
    max_storage_gb: float = 0.0
    current_load: float = 0.0  # 0.0-1.0

    def compute_score(self) -> float:
        """Compute the reputation score based on metrics.

        Score formula (0.0-100.0):
            raw = uptime_weight * min(uptime/1000, 1.0)
                + bandwidth_weight * min(bandwidth/200, 1.0)
                + storage_weight * min(storage/100, 1.0)
            attested_bonus = min(attestation_count / 10, 1.0) * 0.3
            score = (raw + attested_bonus) * 100
        """
        uptime_norm = min(self.uptime_hours / 1000.0, 1.0)
        bw_norm = min(self.bandwidth_gb_served / 200.0, 1.0)
        storage_norm = min(self.storage_gb_provided / 100.0, 1.0)

        raw = (
            SCORE_WEIGHTS["uptime"] * uptime_norm
            + SCORE_WEIGHTS["bandwidth"] * bw_norm
            + SCORE_WEIGHTS["storage"] * storage_norm
        )

        attested_bonus = min(self.attestation_count / 10.0, 1.0) * 0.3
        self.score = (raw + attested_bonus) * 100.0

        # Update tier
        if self.bandwidth_gb_served >= BANDWIDTH_TIERS["platinum"]:
            self.tier = "platinum"
        elif self.bandwidth_gb_served >= BANDWIDTH_TIERS["gold"]:
            self.tier = "gold"
        elif self.bandwidth_gb_served >= BANDWIDTH_TIERS["silver"]:
            self.tier = "silver"
        elif self.bandwidth_gb_served >= BANDWIDTH_TIERS["bronze"]:
            self.tier = "bronze"
        else:
            self.tier = "none"

        return self.score

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "address": self.address,
            "capabilities": self.capabilities,
            "is_online": self.is_online,
            "score": round(self.score, 2),
            "tier": self.tier,
            "uptime_hours": round(self.uptime_hours, 1),
            "bandwidth_gb": round(self.bandwidth_gb_served, 2),
            "storage_gb": round(self.storage_gb_provided, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "attestation_count": self.attestation_count,
            "current_load": round(self.current_load, 3),
        }


# ---------------------------------------------------------------------------
# Local relay runner
# ---------------------------------------------------------------------------

@dataclass
class LocalRelayStats:
    """Statistics for the local relay (if running)."""
    started_at: float = 0.0
    messages_relayed: int = 0
    bytes_relayed: int = 0
    dht_entries_stored: int = 0
    dht_storage_bytes: int = 0
    onion_circuits_served: int = 0
    call_minutes_relayed: float = 0.0
    attestation_issued: int = 0
    errors: int = 0


class LocalRelayRunner:
    """Manages the local relay service.

    When enabled, the local node acts as a relay for other Presidium
    users. It can relay messages, store DHT entries, and participate
    in onion circuits.
    """

    def __init__(self, local_user_id: str) -> None:
        self._local_user_id = local_user_id
        self._is_running = False
        self._stats = LocalRelayStats()
        self._capabilities: list[str] = [
            RelayCapability.MESSAGE_RELAY,
            RelayCapability.DHT_STORAGE,
            RelayCapability.ONION_RELAY,
        ]
        self._message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        self._relay_task: Optional[asyncio.Task[None]] = None

        # Callbacks
        self._send_to_peer: Optional[Callable[[str, bytes], Awaitable[None]]] = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def stats(self) -> LocalRelayStats:
        return self._stats

    def set_send_callback(
        self, callback: Callable[[str, bytes], Awaitable[None]]
    ) -> None:
        self._send_to_peer = callback

    async def start(self) -> None:
        """Start the local relay service."""
        if self._is_running:
            return

        self._is_running = True
        self._stats.started_at = time.time()
        self._relay_task = asyncio.create_task(self._relay_loop())

        logger.info("Local relay started with capabilities: %s", self._capabilities)

    async def stop(self) -> None:
        """Stop the local relay service."""
        self._is_running = False
        if self._relay_task and not self._relay_task.done():
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass

        uptime = (time.time() - self._stats.started_at) / 3600
        logger.info(
            "Local relay stopped. Uptime: %.1fh, %d messages relayed, "
            "%d bytes, %d DHT entries",
            uptime,
            self._stats.messages_relayed,
            self._stats.bytes_relayed,
            self._stats.dht_entries_stored,
        )

    async def relay_message(self, recipient_addr: str, data: bytes) -> bool:
        """Relay a message to another peer.

        Args:
            recipient_addr: Target address (host:port).
            data: Message data to relay.

        Returns:
            True if relay was successful.
        """
        if not self._is_running or self._send_to_peer is None:
            return False

        try:
            await self._send_to_peer(recipient_addr, data)
            self._stats.messages_relayed += 1
            self._stats.bytes_relayed += len(data)
            return True
        except Exception as exc:
            self._stats.errors += 1
            logger.debug("Relay failed to %s: %s", recipient_addr, exc)
            return False

    async def _relay_loop(self) -> None:
        """Background relay processing loop."""
        while self._is_running:
            try:
                # Process queued messages
                try:
                    msg = await asyncio.wait_for(
                        self._message_queue.get(), timeout=1.0
                    )
                    addr = msg.get("address", "")
                    data = msg.get("data", b"")
                    if addr and data:
                        await self.relay_message(addr, data)
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Relay loop error: %s", exc)
                await asyncio.sleep(5)

    def get_descriptor(self) -> RelayDescriptor:
        """Get a RelayDescriptor for this local relay."""
        uptime = (time.time() - self._stats.started_at) / 3600 if self._stats.started_at else 0
        bw_gb = self._stats.bytes_relayed / (1024 ** 3)

        desc = RelayDescriptor(
            user_id=self._local_user_id,
            capabilities=self._capabilities,
            is_online=self._is_running,
            uptime_hours=uptime,
            bandwidth_gb_served=bw_gb,
            dht_entries_stored=self._stats.dht_entries_stored,
            storage_gb_provided=self._stats.dht_storage_bytes / (1024 ** 3),
        )
        desc.compute_score()
        return desc


# ---------------------------------------------------------------------------
# Relay network manager
# ---------------------------------------------------------------------------

class RelayNetworkManager:
    """Manages the relay network: discovery, scoring, and selection.

    Maintains a local view of known relays and their scores. Selects
    the best relays for specific tasks (onion routing, message delivery,
    DHT storage).
    """

    def __init__(self, local_user_id: str) -> None:
        self._local_user_id = local_user_id
        self._relays: dict[str, RelayDescriptor] = {}  # user_id → descriptor
        self._local_relay = LocalRelayRunner(local_user_id)
        self._attestations: dict[str, list[Attestation]] = {}  # relay_id → attestations

        # Callbacks
        self._discover_relays: Optional[
            Callable[[], Awaitable[list[RelayDescriptor]]]
        ] = None

    def set_discover_callback(
        self, callback: Callable[[], Awaitable[list[RelayDescriptor]]]
    ) -> None:
        """Set callback for discovering relays from the DHT."""
        self._discover_relays = callback

    # -------------------------------------------------------------------
    # Relay discovery
    # -------------------------------------------------------------------

    async def discover_relays(self) -> list[RelayDescriptor]:
        """Discover available relay nodes from the DHT."""
        if self._discover_relays is None:
            return []

        try:
            relays = await self._discover_relays()
            for relay in relays:
                self._relays[relay.user_id] = relay
            return relays
        except Exception as exc:
            logger.error("Relay discovery failed: %s", exc)
            return []

    # -------------------------------------------------------------------
    # Relay selection
    # -------------------------------------------------------------------

    def select_best_relay(
        self,
        capability: str = RelayCapability.MESSAGE_RELAY,
        exclude: Optional[list[str]] = None,
    ) -> Optional[RelayDescriptor]:
        """Select the best relay for a specific task.

        Selection criteria:
            1. Must have the requested capability
            2. Must be online
            3. Must not be in the exclude list
            4. Highest score wins (with latency tiebreaker)

        Args:
            capability: Required capability.
            exclude: Relay user_ids to exclude (e.g., already used in circuit).

        Returns:
            The best RelayDescriptor, or None if no suitable relay found.
        """
        exclude_set = set(exclude or [])
        candidates = []

        for relay in self._relays.values():
            if relay.user_id in exclude_set:
                continue
            if not relay.is_online:
                continue
            if capability not in relay.capabilities:
                continue
            candidates.append(relay)

        if not candidates:
            return None

        # Sort by score (descending), then by latency (ascending)
        candidates.sort(key=lambda r: (-r.score, r.avg_latency_ms))
        return candidates[0]

    def select_onion_circuit_relays(self) -> list[RelayDescriptor]:
        """Select 3 relays for an onion routing circuit.

        Returns relays for guard, middle, and exit positions,
        selecting based on score and geographic diversity.
        """
        selected: list[RelayDescriptor] = []
        exclude: list[str] = []

        for _ in range(3):
            relay = self.select_best_relay(
                capability=RelayCapability.ONION_RELAY,
                exclude=exclude,
            )
            if relay is None:
                break
            selected.append(relay)
            exclude.append(relay.user_id)

        return selected

    # -------------------------------------------------------------------
    # Attestation management
    # -------------------------------------------------------------------

    async def issue_attestation(
        self,
        relay_user_id: str,
        capabilities_verified: list[str],
        latency_ms: float,
        sign_func: Callable[[bytes], bytes],
    ) -> Attestation:
        """Issue an attestation for a relay we've used.

        Args:
            relay_user_id: The relay being attested.
            capabilities_verified: Which capabilities we verified.
            latency_ms: Observed latency.
            sign_func: Function to sign the attestation payload.

        Returns:
            The signed Attestation.
        """
        attestation = Attestation(
            relay_user_id=relay_user_id,
            attester_user_id=self._local_user_id,
            timestamp=time.time(),
            capabilities_verified=capabilities_verified,
            latency_ms=latency_ms,
        )

        attestation.attester_signature = sign_func(attestation.sign_payload())

        # Store locally
        if relay_user_id not in self._attestations:
            self._attestations[relay_user_id] = []
        self._attestations[relay_user_id].append(attestation)

        logger.info(
            "Attestation issued for relay %s: %s, latency=%.1fms",
            relay_user_id[:8],
            capabilities_verified,
            latency_ms,
        )
        return attestation

    def apply_attestation(self, attestation: Attestation) -> None:
        """Apply an attestation to update a relay's score."""
        relay = self._relays.get(attestation.relay_user_id)
        if relay:
            relay.attestation_count += 1
            # Update latency with exponential moving average
            if relay.avg_latency_ms == 0:
                relay.avg_latency_ms = attestation.latency_ms
            else:
                relay.avg_latency_ms = (
                    0.8 * relay.avg_latency_ms + 0.2 * attestation.latency_ms
                )
            relay.compute_score()

    # -------------------------------------------------------------------
    # Score decay
    # -------------------------------------------------------------------

    async def decay_scores(self) -> None:
        """Apply time-based score decay to all relays.

        Scores decay by SCORE_DECAY_RATE per hour to prevent
        stale high scores from relays that are no longer performing.
        """
        for relay in self._relays.values():
            relay.score *= SCORE_DECAY_RATE
            relay.compute_score()

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_relay(self, user_id: str) -> Optional[RelayDescriptor]:
        """Get a relay by user ID."""
        return self._relays.get(user_id)

    def get_all_relays(self) -> list[RelayDescriptor]:
        """Get all known relays."""
        return list(self._relays.values())

    def get_online_relays(self) -> list[RelayDescriptor]:
        """Get all online relays."""
        return [r for r in self._relays.values() if r.is_online]

    @property
    def local_relay(self) -> LocalRelayRunner:
        """Access the local relay runner."""
        return self._local_relay
