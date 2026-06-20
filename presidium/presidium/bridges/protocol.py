"""Abstract base protocol for Presidium federation bridges.

Defines the interface that all bridge implementations must conform to,
along with shared data models for cross-protocol message mapping.

Design goals:
    - Uniform lifecycle (start → run → stop) across all bridge types
    - Common message model that normalizes external message formats into
      a Presidium-compatible representation
    - Configurable rate limiting and message size bounds
    - Bidirectional relay with per-bridge identity isolation
    - Async-native: all I/O operations are coroutines
"""

from __future__ import annotations

import abc
import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from presidium.utils.config import AppConfig


# ---------------------------------------------------------------------------
# Bridge message types
# ---------------------------------------------------------------------------

class BridgeMessageType(enum.IntEnum):
    """Type of a bridged message, mirroring Presidium's MessageType."""
    TEXT = 1
    IMAGE = 2
    FILE = 3
    REACTION = 4
    TYPING_START = 5
    TYPING_STOP = 6
    READ_RECEIPT = 7


class BridgeDirection(enum.IntEnum):
    """Direction of a bridged message relative to Presidium."""
    INBOUND = 1   # External → Presidium
    OUTBOUND = 2  # Presidium → External


class BridgeStatus(enum.IntEnum):
    """Runtime status of a bridge instance."""
    STOPPED = 0
    STARTING = 1
    RUNNING = 2
    DEGRADED = 3   # partially connected (e.g. reconnecting)
    ERROR = 4


# ---------------------------------------------------------------------------
# Bridged message model
# ---------------------------------------------------------------------------

@dataclass
class BridgedMessage:
    """Normalized message representation for cross-protocol bridging.

    Every external message is converted into this format before being
    injected into the Presidium messaging layer, and vice versa.
    """
    # Unique ID for this bridged message (16 bytes → 32 hex chars)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:32])

    # External network identifiers
    external_room_id: str = ""       # e.g. Matrix room ID, XMPP MUC JID
    external_sender_id: str = ""     # e.g. @user:matrix.org, user@xmpp.org
    external_event_id: str = ""      # original event ID on the external network

    # Presidium mapping
    presidium_peer_id: str = ""      # Mapped Presidium user_id for the sender
    presidium_session_id: str = ""   # Presidium chat session this maps to

    # Message content
    msg_type: BridgeMessageType = BridgeMessageType.TEXT
    text_content: str = ""
    media_url: str = ""              # URL or mxc:// URI for media
    media_mimetype: str = ""
    media_size: int = 0
    reply_to_external_id: str = ""
    emoji: str = ""                  # For REACTION type

    # Metadata
    timestamp: float = field(default_factory=time.time)
    direction: BridgeDirection = BridgeDirection.INBOUND
    is_encrypted_on_external: bool = False  # Was E2EE used on the external side?
    extra: dict[str, Any] = field(default_factory=dict)  # Bridge-specific metadata


@dataclass
class BridgedRoom:
    """Represents a mapped room/channel between Presidium and an external network.

    A BridgedRoom maps a Presidium peer (or group session) to an external
    room/channel. Messages in one direction are relayed to the other.
    """
    # External identifiers
    external_room_id: str = ""       # e.g. !room:matrix.org, muc@xmpp.org
    external_room_name: str = ""

    # Presidium identifiers
    presidium_peer_id: str = ""      # Peer user_id (1:1) or group session ID
    presidium_session_id: str = ""
    is_group: bool = False           # True if this maps to a group chat

    # Bridge-specific config
    bridge_type: str = ""            # "matrix" | "xmpp" | etc.
    relay_bot_id: str = ""           # The bridge bot's ID on the external network
    membership_synced: bool = False  # Whether member list has been synced
    last_event_ts: float = 0.0       # Last processed event timestamp

    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bridge configuration
# ---------------------------------------------------------------------------

@dataclass
class BridgeConfig:
    """Configuration for a single bridge instance."""
    bridge_type: str = ""              # "matrix" | "xmpp"
    enabled: bool = True

    # Rate limiting
    inbound_rate_limit: int = 20       # Max inbound messages per minute
    outbound_rate_limit: int = 30      # Max outbound messages per minute
    max_message_size: int = 65536      # Max message size in bytes

    # Message relay
    relay_prefix: str = ""             # Prefix for bridged messages, e.g. "[Matrix] "
    relay_display_name: bool = True    # Include sender's display name from external net
    relay_media: bool = True           # Bridge media (images, files)
    relay_reactions: bool = True       # Bridge emoji reactions
    relay_typing: bool = True          # Bridge typing indicators
    relay_read_receipts: bool = False  # Bridge read receipts (privacy concern)

    # Identity mapping
    auto_create_contacts: bool = True  # Auto-create Presidium contacts for external users
    contact_prefix: str = ""           # Prefix for auto-created contact nicknames

    # Connection
    reconnect_backoff_base: float = 2.0
    reconnect_backoff_max: float = 300.0
    max_reconnect_attempts: int = 50

    # Bridge-specific fields (populated by subclasses)
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract bridge interface
# ---------------------------------------------------------------------------

class BaseBridge(abc.ABC):
    """Abstract base class for all Presidium federation bridges.

    A bridge connects the Presidium P2P network to an external messaging
    protocol (Matrix, XMPP, etc.). It provides bidirectional message relay
    while abstracting the specifics of each external protocol.

    Lifecycle:
        1. Instantiate with config and callbacks
        2. Call start() → connects to external network
        3. Messages flow in both directions via callbacks
        4. Call stop() → graceful disconnect

    Subclasses must implement:
        - start(): Establish connection to external network
        - stop(): Gracefully disconnect
        - send_message(): Relay a Presidium message to the external network
        - _run(): Main event loop for receiving external messages

    Thread safety:
        All public methods are coroutines and must be called from the same
        asyncio event loop. The bridge manages its own internal locking
        where needed.
    """

    def __init__(
        self,
        config: BridgeConfig,
        app_config: AppConfig,
        local_user_id: str,
    ) -> None:
        self._config = config
        self._app_config = app_config
        self._local_user_id = local_user_id
        self._status = BridgeStatus.STOPPED
        self._rooms: dict[str, BridgedRoom] = {}  # external_room_id → BridgedRoom
        self._started_at: Optional[float] = None
        self._stats = BridgeStats()

        # Rate limiting state
        self._inbound_timestamps: list[float] = []
        self._outbound_timestamps: list[float] = []

        # Callbacks for injecting messages into Presidium
        self._on_inbound_message: Optional[
            Callable[[BridgedMessage], Awaitable[None]]
        ] = None
        self._on_inbound_typing: Optional[
            Callable[[str, bool], Awaitable[None]]
        ] = None
        self._on_room_created: Optional[
            Callable[[BridgedRoom], Awaitable[None]]
        ] = None
        self._on_status_changed: Optional[
            Callable[[BridgeStatus], Awaitable[None]]
        ] = None

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def bridge_type(self) -> str:
        """Return the bridge type identifier (e.g. 'matrix', 'xmpp')."""
        return self._config.bridge_type

    @property
    def status(self) -> BridgeStatus:
        """Current operational status of this bridge."""
        return self._status

    @property
    def is_running(self) -> bool:
        """True if the bridge is actively running."""
        return self._status in (BridgeStatus.RUNNING, BridgeStatus.DEGRADED)

    @property
    def rooms(self) -> dict[str, BridgedRoom]:
        """All bridged rooms indexed by external room ID."""
        return dict(self._rooms)

    @property
    def stats(self) -> BridgeStats:
        """Runtime statistics for this bridge."""
        return self._stats

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_on_inbound_message(
        self, callback: Callable[[BridgedMessage], Awaitable[None]]
    ) -> None:
        """Set callback for when an external message arrives.

        The bridge calls this after converting the external message into
        a BridgedMessage. The Presidium layer is responsible for routing
        it to the appropriate chat session.
        """
        self._on_inbound_message = callback

    def set_on_inbound_typing(
        self, callback: Callable[[str, bool], Awaitable[None]]
    ) -> None:
        """Set callback for typing indicators from external network.

        Args:
            callback: (external_room_id, is_typing) → None
        """
        self._on_inbound_typing = callback

    def set_on_room_created(
        self, callback: Callable[[BridgedRoom], Awaitable[None]]
    ) -> None:
        """Set callback for when a new bridged room is discovered."""
        self._on_room_created = callback

    def set_on_status_changed(
        self, callback: Callable[[BridgeStatus], Awaitable[None]]
    ) -> None:
        """Set callback for bridge status changes."""
        self._on_status_changed = callback

    # -------------------------------------------------------------------
    # Abstract interface — subclasses MUST implement
    # -------------------------------------------------------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Connect to the external network and start receiving events.

        Must set self._status to BridgeStatus.RUNNING on success, or
        BridgeStatus.ERROR on failure.

        Raises:
            BridgeError: If the connection cannot be established.
        """

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect from the external network.

        Must set self._status to BridgeStatus.STOPPED.
        """

    @abc.abstractmethod
    async def send_message(self, message: BridgedMessage) -> str:
        """Send a message from Presidium to the external network.

        Args:
            message: The bridged message to send (direction=OUTBOUND).

        Returns:
            The external event ID assigned by the external network.

        Raises:
            BridgeError: If the message cannot be sent.
            BridgeRateLimitError: If the outbound rate limit is exceeded.
        """

    @abc.abstractmethod
    async def create_room(self, presidium_peer_id: str, is_group: bool = False) -> BridgedRoom:
        """Create a bridged room on the external network.

        Args:
            presidium_peer_id: The Presidium peer to bridge.
            is_group: True if this is a group chat.

        Returns:
            The newly created BridgedRoom.

        Raises:
            BridgeError: If the room cannot be created.
        """

    @abc.abstractmethod
    async def get_user_profile(self, external_user_id: str) -> dict[str, Any]:
        """Fetch a user's profile from the external network.

        Args:
            external_user_id: User ID on the external network.

        Returns:
            Dict with at minimum 'display_name' and 'avatar_url' keys.
            Values may be empty strings if unavailable.
        """

    # -------------------------------------------------------------------
    # Shared rate limiting
    # -------------------------------------------------------------------

    def _check_inbound_rate_limit(self) -> bool:
        """Check if an inbound message is within the rate limit.

        Returns:
            True if the message is allowed, False if rate-limited.
        """
        now = time.time()
        cutoff = now - 60.0  # 1-minute window
        self._inbound_timestamps = [
            ts for ts in self._inbound_timestamps if ts > cutoff
        ]
        if len(self._inbound_timestamps) >= self._config.inbound_rate_limit:
            self._stats.inbound_rate_limited += 1
            return False
        self._inbound_timestamps.append(now)
        return True

    def _check_outbound_rate_limit(self) -> bool:
        """Check if an outbound message is within the rate limit.

        Returns:
            True if the message is allowed, False if rate-limited.
        """
        now = time.time()
        cutoff = now - 60.0
        self._outbound_timestamps = [
            ts for ts in self._outbound_timestamps if ts > cutoff
        ]
        if len(self._outbound_timestamps) >= self._config.outbound_rate_limit:
            self._stats.outbound_rate_limited += 1
            return False
        self._outbound_timestamps.append(now)
        return True

    # -------------------------------------------------------------------
    # Shared status management
    # -------------------------------------------------------------------

    async def _set_status(self, new_status: BridgeStatus) -> None:
        """Update bridge status and notify callback."""
        old_status = self._status
        self._status = new_status
        if old_status != new_status and self._on_status_changed:
            await self._on_status_changed(new_status)

    # -------------------------------------------------------------------
    # Shared message dispatch helpers
    # -------------------------------------------------------------------

    async def _dispatch_inbound(self, message: BridgedMessage) -> None:
        """Validate and dispatch an inbound message to Presidium.

        Applies rate limiting and size checks before invoking the
        on_inbound_message callback.
        """
        if not self._check_inbound_rate_limit():
            self._stats.inbound_dropped += 1
            return

        if len(message.text_content.encode("utf-8")) > self._config.max_message_size:
            self._stats.inbound_dropped += 1
            return

        self._stats.inbound_relayed += 1
        if self._on_inbound_message:
            await self._on_inbound_message(message)

    async def _dispatch_typing(self, external_room_id: str, is_typing: bool) -> None:
        """Dispatch a typing indicator to Presidium."""
        if not self._config.relay_typing:
            return
        if self._on_inbound_typing:
            await self._on_inbound_typing(external_room_id, is_typing)

    # -------------------------------------------------------------------
    # Room management
    # -------------------------------------------------------------------

    def _register_room(self, room: BridgedRoom) -> None:
        """Register a bridged room."""
        self._rooms[room.external_room_id] = room

    def get_room(self, external_room_id: str) -> Optional[BridgedRoom]:
        """Look up a bridged room by its external ID."""
        return self._rooms.get(external_room_id)

    def get_room_by_presidium_peer(self, presidium_peer_id: str) -> Optional[BridgedRoom]:
        """Look up a bridged room by the Presidium peer ID it maps to."""
        for room in self._rooms.values():
            if room.presidium_peer_id == presidium_peer_id:
                return room
        return None

    # -------------------------------------------------------------------
    # Exponential backoff helper
    # -------------------------------------------------------------------

    def _compute_backoff(self, attempt: int) -> float:
        """Compute exponential backoff delay in seconds."""
        import random
        delay = min(
            self._config.reconnect_backoff_base * (2 ** attempt),
            self._config.reconnect_backoff_max,
        )
        # Jitter ±25%
        jitter = delay * 0.25 * (random.random() * 2 - 1)
        return max(0.1, delay + jitter)


# ---------------------------------------------------------------------------
# Bridge statistics
# ---------------------------------------------------------------------------

@dataclass
class BridgeStats:
    """Runtime statistics for a bridge instance."""
    inbound_relayed: int = 0
    inbound_dropped: int = 0
    inbound_rate_limited: int = 0
    outbound_relayed: int = 0
    outbound_failed: int = 0
    outbound_rate_limited: int = 0
    rooms_active: int = 0
    last_inbound_ts: float = 0.0
    last_outbound_ts: float = 0.0
    reconnect_count: int = 0
    uptime_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Bridge exceptions
# ---------------------------------------------------------------------------

class BridgeError(Exception):
    """Base exception for bridge operations."""


class BridgeConnectionError(BridgeError):
    """Failed to connect to the external network."""


class BridgeAuthError(BridgeError):
    """Authentication failed on the external network."""


class BridgeRateLimitError(BridgeError):
    """Rate limit exceeded on the external network."""


class BridgeMessageError(BridgeError):
    """Failed to send or receive a message through the bridge."""


class BridgeRoomError(BridgeError):
    """Failed to create or manage a bridged room."""


class BridgeConfigError(BridgeError):
    """Invalid bridge configuration."""


# ---------------------------------------------------------------------------
# Bridge registry
# ---------------------------------------------------------------------------

_BRIDGE_REGISTRY: dict[str, type[BaseBridge]] = {}


def register_bridge(bridge_type: str) -> Callable[[type[BaseBridge]], type[BaseBridge]]:
    """Decorator to register a bridge implementation.

    Usage:
        @register_bridge("matrix")
        class MatrixBridge(BaseBridge):
            ...
    """
    def decorator(cls: type[BaseBridge]) -> type[BaseBridge]:
        _BRIDGE_REGISTRY[bridge_type] = cls
        return cls
    return decorator


def get_bridge_class(bridge_type: str) -> type[BaseBridge]:
    """Look up a registered bridge class by type name."""
    cls = _BRIDGE_REGISTRY.get(bridge_type)
    if cls is None:
        raise BridgeConfigError(
            f"No bridge registered for type {bridge_type!r}. "
            f"Available: {list(_BRIDGE_REGISTRY.keys())}"
        )
    return cls


def list_bridge_types() -> list[str]:
    """Return a list of all registered bridge type names."""
    return list(_BRIDGE_REGISTRY.keys())
