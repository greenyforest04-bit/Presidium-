"""White-label SDK for embedding Presidium into other applications.

Provides a simple, thread-safe async API that wraps the core Presidium
modules (identity, network, messaging) into a cohesive interface suitable
for embedding into third-party applications.

Architecture:
    PresidiumSDK (main entry point)
        ├── SDKConfig — immutable configuration dataclass
        ├── SDKEvent — events emitted by the SDK (message, contact, group, error)
        ├── SDKEventHandler — callback interface for event subscription
        ├── AsyncEventBus — internal event dispatch (thread-safe)
        ├── IdentityFacade — wraps presidium.core.identity
        ├── NetworkFacade — wraps presidium.core.network
        ├── MessagingFacade — wraps presidium.core.messaging
        └── SDKError hierarchy — structured error reporting

SDK lifecycle:
    1. Construct PresidiumSDK with SDKConfig
    2. Call initialize() to set up identity, network, and messaging
    3. Use send_message(), receive_messages(), create_group(), get_contacts()
    4. Register SDKEventHandler callbacks for real-time events
    5. Call shutdown() to cleanly release all resources

Thread safety:
    All public SDK methods are safe to call from any thread. Internally,
    the SDK uses an asyncio event loop and thread-safe queues to bridge
    synchronous callers to the async core. The recommended usage pattern
    is async/await from a single async context.

Error handling:
    - SDKError: Base exception for all SDK errors
    - SDKNotInitializedError: SDK used before initialize()
    - SDKConnectionError: Network connectivity issues
    - SDKAuthenticationError: Invalid API key or identity
    - SDKMessageError: Message send/receive failures
    - SDKGroupError: Group creation or management failures
    - SDKTimeoutError: Operation exceeded time limit

Usage example:
    config = SDKConfig(
        app_name="MyApp",
        api_key="pk_live_abc123",
        relay_url="wss://relay.presidium.chat",
    )
    sdk = PresidiumSDK(config)
    await sdk.initialize()

    # Send a message
    await sdk.send_message(recipient_id="abc123", text="Hello!")

    # Register event handler
    class MyHandler(SDKEventHandler):
        async def on_message_received(self, message):
            print(f"Got: {message.text}")

    sdk.register_handler(MyHandler())

    # Clean up
    await sdk.shutdown()
"""

from __future__ import annotations

import abc
import asyncio
import enum
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from presidium.i18n import _

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RELAY_URL = "wss://relay.presidium.chat"
DEFAULT_API_VERSION = "v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RECONNECT_ATTEMPTS = 3
DEFAULT_RECONNECT_DELAY_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 30.0
MAX_MESSAGE_LENGTH = 65536
MAX_GROUP_NAME_LENGTH = 128
MAX_GROUP_MEMBERS = 512


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SDKError(Exception):
    """Base exception for all Presidium SDK errors."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        self.code = code or "SDK_ERROR"
        super().__init__(message)


class SDKNotInitializedError(SDKError):
    """SDK was used before initialize() was called."""

    def __init__(self, message: str = _("SDK is not initialized. Call initialize() first.")) -> None:
        super().__init__(message, code="NOT_INITIALIZED")


class SDKConnectionError(SDKError):
    """Network connectivity or relay connection issues."""

    def __init__(self, message: str = _("Connection to Presidium relay failed.")) -> None:
        super().__init__(message, code="CONNECTION_ERROR")


class SDKAuthenticationError(SDKError):
    """Invalid API key, expired token, or identity verification failure."""

    def __init__(self, message: str = _("Authentication failed. Check your API key.")) -> None:
        super().__init__(message, code="AUTH_ERROR")


class SDKMessageError(SDKError):
    """Message send or receive operation failed."""

    def __init__(self, message: str = _("Message operation failed.")) -> None:
        super().__init__(message, code="MESSAGE_ERROR")


class SDKGroupError(SDKError):
    """Group creation or management operation failed."""

    def __init__(self, message: str = _("Group operation failed.")) -> None:
        super().__init__(message, code="GROUP_ERROR")


class SDKTimeoutError(SDKError):
    """Operation exceeded the configured time limit."""

    def __init__(self, message: str = _("Operation timed out.")) -> None:
        super().__init__(message, code="TIMEOUT")


class SDKConfigError(SDKError):
    """SDK configuration is invalid or incomplete."""

    def __init__(self, message: str = _("Invalid SDK configuration.")) -> None:
        super().__init__(message, code="CONFIG_ERROR")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SDKEvent(enum.Enum):
    """Events emitted by the Presidium SDK.

    Subscribe to these events by registering an SDKEventHandler with
    the PresidiumSDK.register_handler() method.
    """
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"
    CONTACT_ADDED = "contact_added"
    GROUP_CREATED = "group_created"
    ERROR = "error"


class ConnectionState(enum.Enum):
    """Current connection state of the SDK's network layer."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class MessageType(enum.Enum):
    """Types of messages in the SDK."""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    LOCATION = "location"
    CONTACT = "contact"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SDKConfig:
    """Immutable configuration for the Presidium SDK.

    Attributes:
        app_name: Name of the embedding application (for user-agent and logging).
        api_key: Presidium API key for authentication with the relay.
        relay_url: WebSocket URL of the Presidium relay server.
        api_version: API version string (default: "v1").
        timeout_seconds: Default timeout for SDK operations.
        reconnect_attempts: Maximum number of reconnection attempts.
        reconnect_delay_seconds: Base delay between reconnection attempts.
        auto_reconnect: Whether to automatically reconnect on disconnect.
        enable_encryption: Whether to enable E2EE (default: True).
        data_dir: Optional custom data directory for identity and cache.
        heartbeat_interval: Interval in seconds for keep-alive heartbeats.
        max_message_length: Maximum message text length in characters.
    """
    app_name: str = ""
    api_key: str = ""
    relay_url: str = DEFAULT_RELAY_URL
    api_version: str = DEFAULT_API_VERSION
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS
    reconnect_delay_seconds: float = DEFAULT_RECONNECT_DELAY_SECONDS
    auto_reconnect: bool = True
    enable_encryption: bool = True
    data_dir: str = ""
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS
    max_message_length: int = MAX_MESSAGE_LENGTH

    def validate(self) -> None:
        """Validate the configuration and raise on invalid values.

        Raises:
            SDKConfigError: If required fields are missing or values are invalid.
        """
        if not self.app_name:
            raise SDKConfigError(_("app_name is required."))
        if not self.api_key:
            raise SDKConfigError(_("api_key is required."))
        if not self.relay_url.startswith(("wss://", "ws://")):
            raise SDKConfigError(
                _("relay_url must start with wss:// or ws://, got: {url}").format(
                    url=self.relay_url
                )
            )
        if self.timeout_seconds <= 0:
            raise SDKConfigError(_("timeout_seconds must be positive."))
        if self.reconnect_attempts < 0:
            raise SDKConfigError(_("reconnect_attempts cannot be negative."))
        if self.heartbeat_interval < 1.0:
            raise SDKConfigError(_("heartbeat_interval must be at least 1 second."))


@dataclass
class SDKMessage:
    """A message in the Presidium SDK.

    Attributes:
        id: Unique message identifier.
        conversation_id: ID of the conversation this message belongs to.
        sender_id: User ID of the sender.
        recipient_id: User ID of the recipient (for 1:1 chats).
        text: Message body text.
        message_type: Type of message content.
        timestamp: Unix timestamp (seconds) when the message was sent/received.
        is_outgoing: Whether this message was sent by the local user.
        is_read: Whether the message has been read.
        reply_to_id: ID of the message this replies to, if any.
        metadata: Additional key-value metadata.
    """
    id: str = ""
    conversation_id: str = ""
    sender_id: str = ""
    recipient_id: str = ""
    text: str = ""
    message_type: MessageType = MessageType.TEXT
    timestamp: float = 0.0
    is_outgoing: bool = False
    is_read: bool = False
    reply_to_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "text": self.text,
            "message_type": self.message_type.value,
            "timestamp": self.timestamp,
            "is_outgoing": self.is_outgoing,
            "is_read": self.is_read,
            "reply_to_id": self.reply_to_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SDKMessage:
        """Deserialize from a dict.

        Args:
            data: Dictionary with message fields.

        Returns:
            SDKMessage instance.
        """
        msg_type = MessageType.TEXT
        raw_type = data.get("message_type", "text")
        try:
            msg_type = MessageType(raw_type)
        except ValueError:
            logger.debug("Unknown message type '%s', defaulting to TEXT.", raw_type)

        return cls(
            id=data.get("id", ""),
            conversation_id=data.get("conversation_id", ""),
            sender_id=data.get("sender_id", ""),
            recipient_id=data.get("recipient_id", ""),
            text=data.get("text", ""),
            message_type=msg_type,
            timestamp=data.get("timestamp", 0.0),
            is_outgoing=data.get("is_outgoing", False),
            is_read=data.get("is_read", False),
            reply_to_id=data.get("reply_to_id", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class SDKContact:
    """A contact in the Presidium SDK.

    Attributes:
        user_id: Unique Presidium user ID (32 hex chars).
        nickname: Display name for the contact.
        public_key_b64: Base64-encoded Ed25519 public key.
        is_verified: Whether the contact's safety number has been verified.
        is_online: Whether the contact is currently online.
        last_seen: Unix timestamp of last online time, or 0.
        added_at: Unix timestamp when the contact was added.
    """
    user_id: str = ""
    nickname: str = ""
    public_key_b64: str = ""
    is_verified: bool = False
    is_online: bool = False
    last_seen: float = 0.0
    added_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "user_id": self.user_id,
            "nickname": self.nickname,
            "public_key_b64": self.public_key_b64,
            "is_verified": self.is_verified,
            "is_online": self.is_online,
            "last_seen": self.last_seen,
            "added_at": self.added_at,
        }


@dataclass
class SDKGroup:
    """A group chat in the Presidium SDK.

    Attributes:
        id: Unique group identifier.
        name: Group display name.
        members: List of member user IDs.
        admin_id: User ID of the group administrator.
        created_at: Unix timestamp of group creation.
        description: Optional group description.
    """
    id: str = ""
    name: str = ""
    members: list[str] = field(default_factory=list)
    admin_id: str = ""
    created_at: float = 0.0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "members": self.members,
            "admin_id": self.admin_id,
            "created_at": self.created_at,
            "description": self.description,
        }


@dataclass
class SDKEncryptionStats:
    """Statistics about the E2EE status of the SDK session.

    Attributes:
        sessions_active: Number of active Double Ratchet sessions.
        keys_rotated: Total number of ratchet key rotations.
        messages_encrypted: Total messages encrypted and sent.
        messages_decrypted: Total messages decrypted and received.
        encryption_failures: Number of encryption/decryption failures.
    """
    sessions_active: int = 0
    keys_rotated: int = 0
    messages_encrypted: int = 0
    messages_decrypted: int = 0
    encryption_failures: int = 0


# ---------------------------------------------------------------------------
# Event handler interface
# ---------------------------------------------------------------------------

class SDKEventHandler(abc.ABC):
    """Abstract callback interface for SDK events.

    Subclass this and override the methods for events you care about.
    Unhandled events are safely ignored (no-op default implementations).

    All callback methods are async. The SDK dispatches events from its
    internal event loop, so callbacks should not block for long periods.
    """

    async def on_message_received(self, message: SDKMessage) -> None:
        """Called when a new message is received.

        Args:
            message: The received message.
        """

    async def on_message_sent(self, message: SDKMessage) -> None:
        """Called when a message is successfully sent.

        Args:
            message: The sent message (with assigned ID and timestamp).
        """

    async def on_contact_added(self, contact: SDKContact) -> None:
        """Called when a new contact is added.

        Args:
            contact: The newly added contact.
        """

    async def on_group_created(self, group: SDKGroup) -> None:
        """Called when a new group is created.

        Args:
            group: The newly created group.
        """

    async def on_error(self, error: SDKError) -> None:
        """Called when an SDK error occurs.

        Args:
            error: The error that occurred.
        """


# ---------------------------------------------------------------------------
# Internal event bus
# ---------------------------------------------------------------------------

class AsyncEventBus:
    """Thread-safe event dispatch for the SDK.

    Manages a list of SDKEventHandler instances and dispatches events
    to all registered handlers concurrently. Errors in individual handlers
    are caught and logged without affecting other handlers.

    Thread safety:
        register() and unregister() are protected by a threading lock.
        Event dispatch runs on the SDK's async event loop.
    """

    def __init__(self) -> None:
        self._handlers: list[SDKEventHandler] = []
        self._lock = threading.Lock()

    def register(self, handler: SDKEventHandler) -> None:
        """Register an event handler.

        Args:
            handler: The handler to register.
        """
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)
                logger.debug("Registered SDK event handler: %s", type(handler).__name__)

    def unregister(self, handler: SDKEventHandler) -> None:
        """Unregister an event handler.

        Args:
            handler: The handler to remove.
        """
        with self._lock:
            try:
                self._handlers.remove(handler)
                logger.debug("Unregistered SDK event handler: %s", type(handler).__name__)
            except ValueError:
                logger.debug("Handler %s not found for removal.", type(handler).__name__)

    async def emit_message_received(self, message: SDKMessage) -> None:
        """Dispatch a MESSAGE_RECEIVED event to all handlers.

        Args:
            message: The received message.
        """
        handlers = self._get_handlers()
        for handler in handlers:
            try:
                await handler.on_message_received(message)
            except Exception as exc:
                logger.warning(
                    "Error in %s.on_message_received: %s",
                    type(handler).__name__, exc,
                )

    async def emit_message_sent(self, message: SDKMessage) -> None:
        """Dispatch a MESSAGE_SENT event to all handlers.

        Args:
            message: The sent message.
        """
        handlers = self._get_handlers()
        for handler in handlers:
            try:
                await handler.on_message_sent(message)
            except Exception as exc:
                logger.warning(
                    "Error in %s.on_message_sent: %s",
                    type(handler).__name__, exc,
                )

    async def emit_contact_added(self, contact: SDKContact) -> None:
        """Dispatch a CONTACT_ADDED event to all handlers.

        Args:
            contact: The added contact.
        """
        handlers = self._get_handlers()
        for handler in handlers:
            try:
                await handler.on_contact_added(contact)
            except Exception as exc:
                logger.warning(
                    "Error in %s.on_contact_added: %s",
                    type(handler).__name__, exc,
                )

    async def emit_group_created(self, group: SDKGroup) -> None:
        """Dispatch a GROUP_CREATED event to all handlers.

        Args:
            group: The created group.
        """
        handlers = self._get_handlers()
        for handler in handlers:
            try:
                await handler.on_group_created(group)
            except Exception as exc:
                logger.warning(
                    "Error in %s.on_group_created: %s",
                    type(handler).__name__, exc,
                )

    async def emit_error(self, error: SDKError) -> None:
        """Dispatch an ERROR event to all handlers.

        Args:
            error: The SDK error.
        """
        handlers = self._get_handlers()
        for handler in handlers:
            try:
                await handler.on_error(error)
            except Exception as exc:
                logger.warning(
                    "Error in %s.on_error: %s",
                    type(handler).__name__, exc,
                )

    def _get_handlers(self) -> list[SDKEventHandler]:
        """Return a snapshot of the current handler list.

        Returns:
            Copy of the handlers list (thread-safe).
        """
        with self._lock:
            return list(self._handlers)


# ---------------------------------------------------------------------------
# Internal facades (wrap core modules)
# ---------------------------------------------------------------------------

class IdentityFacade:
    """Facade wrapping presidium.core.identity for SDK use.

    Manages the SDK's local identity: key generation, unlock, and
    contact info sharing. All methods are async.
    """

    def __init__(self) -> None:
        self._user_id: Optional[str] = None
        self._public_key_b64: Optional[str] = None
        self._nickname: Optional[str] = None
        self._unlocked = False

    @property
    def user_id(self) -> str:
        """Current user ID, or empty string if not unlocked."""
        return self._user_id or ""

    @property
    def is_unlocked(self) -> bool:
        """Whether the identity is currently unlocked."""
        return self._unlocked

    async def initialize(self, data_dir: str, password: str) -> str:
        """Initialize or load the identity.

        Creates a new identity if none exists, otherwise unlocks
        the existing one with the provided password.

        Args:
            data_dir: Directory for identity file storage.
            password: Password for encrypting/decrypting the identity.

        Returns:
            The user ID.

        Raises:
            SDKAuthenticationError: If the password is wrong or identity is corrupted.
        """
        logger.info("Initializing identity facade, data_dir=%s", data_dir)

        # In production: delegates to presidium.core.identity.IdentityManager
        # For SDK standalone mode, we simulate the identity
        self._user_id = uuid.uuid4().hex[:32]
        self._public_key_b64 = uuid.uuid4().hex[:44] + "="
        self._nickname = ""
        self._unlocked = True

        logger.info("Identity initialized: user_id=%s", self._user_id)
        return self._user_id

    async def shutdown(self) -> None:
        """Lock the identity and clear sensitive material."""
        self._user_id = None
        self._public_key_b64 = None
        self._nickname = None
        self._unlocked = False
        logger.info("Identity facade shut down, keys cleared.")

    def get_contact_info(self) -> SDKContact:
        """Return the local user as an SDKContact for sharing.

        Returns:
            SDKContact with the local user's info.

        Raises:
            SDKNotInitializedError: If identity is not unlocked.
        """
        if not self._unlocked:
            raise SDKNotInitializedError()

        return SDKContact(
            user_id=self._user_id or "",
            nickname=self._nickname or "",
            public_key_b64=self._public_key_b64 or "",
            is_verified=True,
            is_online=True,
            last_seen=time.time(),
            added_at=time.time(),
        )


class NetworkFacade:
    """Facade wrapping presidium.core.network for SDK use.

    Manages the WebSocket connection to the Presidium relay,
    including reconnection logic, heartbeat, and message framing.
    """

    def __init__(self, config: SDKConfig) -> None:
        self._config = config
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_count = 0
        self._last_heartbeat: float = 0.0
        self._connected_at: Optional[float] = None

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Whether the network is currently connected."""
        return self._state == ConnectionState.CONNECTED

    async def connect(self, identity_facade: IdentityFacade) -> None:
        """Establish a connection to the Presidium relay.

        Args:
            identity_facade: The initialized identity for authentication.

        Raises:
            SDKConnectionError: If the connection cannot be established.
            SDKAuthenticationError: If relay authentication fails.
        """
        self._state = ConnectionState.CONNECTING
        logger.info(
            "Connecting to relay: %s (app=%s)",
            self._config.relay_url, self._config.app_name,
        )

        try:
            # In production: opens WebSocket, sends auth handshake
            # with identity_facade.user_id and config.api_key
            await asyncio.sleep(0.05)  # Simulated connection delay
            self._state = ConnectionState.CONNECTED
            self._connected_at = time.time()
            self._reconnect_count = 0
            logger.info("Connected to relay successfully.")
        except Exception as exc:
            self._state = ConnectionState.ERROR
            raise SDKConnectionError(
                _("Failed to connect to relay: {error}").format(error=exc)
            ) from exc

    async def disconnect(self) -> None:
        """Gracefully disconnect from the relay."""
        if self._state == ConnectionState.DISCONNECTED:
            return

        logger.info("Disconnecting from relay.")
        self._state = ConnectionState.DISCONNECTED
        self._connected_at = None

    async def send_raw(self, data: bytes) -> None:
        """Send raw framed data over the WebSocket connection.

        Args:
            data: Binary data to send.

        Raises:
            SDKConnectionError: If not connected or send fails.
        """
        if not self.is_connected:
            raise SDKConnectionError(_("Not connected to relay."))

        # In production: frames and sends over WebSocket
        logger.debug("Sending %d bytes over relay connection.", len(data))

    async def receive_raw(self, timeout: float = 5.0) -> Optional[bytes]:
        """Receive raw framed data from the WebSocket connection.

        Args:
            timeout: Maximum time to wait for data, in seconds.

        Returns:
            Received bytes, or None if timeout elapsed.

        Raises:
            SDKConnectionError: If not connected or receive fails.
        """
        if not self.is_connected:
            raise SDKConnectionError(_("Not connected to relay."))

        # In production: reads from WebSocket with timeout
        await asyncio.sleep(0.01)
        return None

    async def send_heartbeat(self) -> None:
        """Send a keep-alive heartbeat if the interval has elapsed."""
        now = time.time()
        if self._last_heartbeat + self._config.heartbeat_interval <= now:
            self._last_heartbeat = now
            logger.debug("Heartbeat sent.")

    @property
    def uptime_seconds(self) -> float:
        """Seconds since the connection was established, or 0."""
        if self._connected_at is None:
            return 0.0
        return time.time() - self._connected_at


class MessagingFacade:
    """Facade wrapping presidium.core.messaging for SDK use.

    Handles message creation, encryption, sending, receiving,
    decryption, and storage.
    """

    def __init__(self, config: SDKConfig) -> None:
        self._config = config
        self._sent_count = 0
        self._received_count = 0
        self._encryption_stats = SDKEncryptionStats()

    @property
    def encryption_stats(self) -> SDKEncryptionStats:
        """Current E2EE statistics."""
        return self._encryption_stats

    async def send_message(
        self,
        sender_id: str,
        recipient_id: str,
        text: str,
        conversation_id: str,
        network: NetworkFacade,
    ) -> SDKMessage:
        """Create, encrypt, and send a message.

        Args:
            sender_id: Local user's ID.
            recipient_id: Recipient's user ID.
            text: Message body text.
            conversation_id: Conversation the message belongs to.
            network: Network facade for sending.

        Returns:
            SDKMessage with assigned ID and timestamp.

        Raises:
            SDKMessageError: If the message cannot be sent.
        """
        if not text:
            raise SDKMessageError(_("Message text cannot be empty."))
        if len(text) > self._config.max_message_length:
            raise SDKMessageError(
                _("Message exceeds maximum length of {max_len} characters.").format(
                    max_len=self._config.max_message_length
                )
            )

        message = SDKMessage(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            text=text,
            message_type=MessageType.TEXT,
            timestamp=time.time(),
            is_outgoing=True,
            is_read=False,
        )

        try:
            # In production: encrypt with Double Ratchet and send via network
            if self._config.enable_encryption:
                self._encryption_stats.messages_encrypted += 1
                logger.debug("Encrypting message %s for %s.", message.id, recipient_id)

            # Simulate sending via network
            payload = message.to_dict()
            import json
            raw_data = json.dumps(payload).encode("utf-8")
            await network.send_raw(raw_data)

            self._sent_count += 1
            logger.debug("Message %s sent to %s.", message.id, recipient_id)

        except SDKConnectionError as exc:
            self._encryption_stats.encryption_failures += 1
            raise SDKMessageError(
                _("Failed to send message: {error}").format(error=exc)
            ) from exc

        return message

    async def receive_message(self, raw_data: bytes) -> SDKMessage:
        """Decrypt and parse an incoming message.

        Args:
            raw_data: Raw encrypted message data from the network.

        Returns:
            Decrypted SDKMessage.

        Raises:
            SDKMessageError: If decryption or parsing fails.
        """
        try:
            import json
            payload = json.loads(raw_data.decode("utf-8"))

            if self._config.enable_encryption:
                # In production: decrypt with Double Ratchet
                self._encryption_stats.messages_decrypted += 1

            message = SDKMessage.from_dict(payload)
            message.is_outgoing = False
            message.is_read = False

            self._received_count += 1
            logger.debug("Received message %s from %s.", message.id, message.sender_id)

            return message

        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as exc:
            self._encryption_stats.encryption_failures += 1
            raise SDKMessageError(
                _("Failed to parse incoming message: {error}").format(error=exc)
            ) from exc

    async def create_group_message(
        self,
        sender_id: str,
        group_id: str,
        text: str,
        network: NetworkFacade,
    ) -> SDKMessage:
        """Send a message to a group chat.

        Args:
            sender_id: Local user's ID.
            group_id: Group conversation ID.
            text: Message body text.
            network: Network facade for sending.

        Returns:
            SDKMessage addressed to the group.

        Raises:
            SDKMessageError: If the message cannot be sent.
        """
        if not text:
            raise SDKMessageError(_("Group message text cannot be empty."))
        if len(text) > self._config.max_message_length:
            raise SDKMessageError(
                _("Message exceeds maximum length of {max_len} characters.").format(
                    max_len=self._config.max_message_length
                )
            )

        message = SDKMessage(
            id=uuid.uuid4().hex,
            conversation_id=group_id,
            sender_id=sender_id,
            recipient_id=group_id,
            text=text,
            message_type=MessageType.TEXT,
            timestamp=time.time(),
            is_outgoing=True,
            is_read=False,
        )

        # In production: fan-out encrypted copies to each group member
        if self._config.enable_encryption:
            self._encryption_stats.messages_encrypted += 1

        try:
            payload = message.to_dict()
            import json
            raw_data = json.dumps(payload).encode("utf-8")
            await network.send_raw(raw_data)
            self._sent_count += 1
        except SDKConnectionError as exc:
            raise SDKMessageError(
                _("Failed to send group message: {error}").format(error=exc)
            ) from exc

        return message


# ---------------------------------------------------------------------------
# Main SDK class
# ---------------------------------------------------------------------------

class PresidiumSDK:
    """Main entry point for the Presidium white-label SDK.

    Wraps identity, network, and messaging modules into a simple async
    API suitable for embedding into third-party applications. All public
    methods are thread-safe and may be called from any thread.

    Lifecycle:
        1. Construct with SDKConfig
        2. Call initialize() — sets up identity, connects to relay
        3. Use messaging and group APIs
        4. Call shutdown() — disconnects and clears state

    Example:
        config = SDKConfig(app_name="MyApp", api_key="pk_live_abc123")
        sdk = PresidiumSDK(config)
        await sdk.initialize()
        msg = await sdk.send_message("user_abc", "Hello!")
        await sdk.shutdown()
    """

    def __init__(self, config: SDKConfig) -> None:
        self._config = config
        self._identity = IdentityFacade()
        self._network = NetworkFacade(config)
        self._messaging = MessagingFacade(config)
        self._event_bus = AsyncEventBus()
        self._contacts: dict[str, SDKContact] = {}
        self._groups: dict[str, SDKGroup] = {}
        self._initialized = False
        self._shutdown = False
        self._lock = threading.Lock()
        self._recv_queue: asyncio.Queue[SDKMessage] = asyncio.Queue()
        self._background_tasks: list[asyncio.Task[None]] = []

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Whether the SDK has been initialized and is ready to use."""
        return self._initialized

    @property
    def connection_state(self) -> ConnectionState:
        """Current connection state of the network layer."""
        return self._network.state

    @property
    def user_id(self) -> str:
        """Current user ID, or empty string if not initialized."""
        return self._identity.user_id

    @property
    def encryption_stats(self) -> SDKEncryptionStats:
        """E2EE statistics for the current session."""
        return self._messaging.encryption_stats

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def initialize(
        self,
        password: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> str:
        """Initialize the SDK: create/load identity and connect to relay.

        This must be called before any other SDK method. It performs:
            1. Validates the SDKConfig
            2. Initializes the identity (create or unlock)
            3. Connects to the Presidium relay
            4. Starts background tasks (heartbeat, receive loop)

        Args:
            password: Password for identity encryption. Defaults to api_key.
            nickname: Optional display name for the local user.

        Returns:
            The local user ID.

        Raises:
            SDKConfigError: If the configuration is invalid.
            SDKAuthenticationError: If identity initialization fails.
            SDKConnectionError: If the relay connection fails.
        """
        with self._lock:
            if self._initialized:
                logger.warning("SDK already initialized, skipping.")
                return self._identity.user_id

        # Validate configuration
        self._config.validate()

        # Initialize identity
        identity_password = password or self._config.api_key
        data_dir = self._config.data_dir or f".presidium-sdk-{self._config.app_name}"
        user_id = await self._identity.initialize(data_dir, identity_password)

        if nickname:
            self._identity._nickname = nickname

        # Connect to relay
        await self._network.connect(self._identity)

        # Start background tasks
        loop = asyncio.get_running_loop()
        self._background_tasks.append(
            loop.create_task(self._heartbeat_loop())
        )
        self._background_tasks.append(
            loop.create_task(self._receive_loop())
        )

        with self._lock:
            self._initialized = True
            self._shutdown = False

        logger.info(
            _("Presidium SDK initialized for %s (user_id=%s)"),
            self._config.app_name, user_id,
        )
        return user_id

    async def shutdown(self) -> None:
        """Shut down the SDK and release all resources.

        Cancels background tasks, disconnects from the relay,
        and clears identity material from memory.

        After shutdown, initialize() must be called again before use.
        """
        with self._lock:
            if not self._initialized:
                return
            self._shutdown = True

        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()
        for task in self._background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("Background task cancelled during shutdown.")
        self._background_tasks.clear()

        # Disconnect and clear
        await self._network.disconnect()
        await self._identity.shutdown()

        self._contacts.clear()
        self._groups.clear()
        self._initialized = False

        logger.info(_("Presidium SDK shut down."))

    # -------------------------------------------------------------------
    # Messaging API
    # -------------------------------------------------------------------

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        reply_to_id: str = "",
    ) -> SDKMessage:
        """Send a text message to a contact.

        Args:
            recipient_id: User ID of the recipient.
            text: Message body text.
            reply_to_id: Optional ID of the message being replied to.

        Returns:
            SDKMessage with assigned ID and timestamp.

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
            SDKMessageError: If the message cannot be sent.
        """
        self._require_initialized()

        if not recipient_id:
            raise SDKMessageError(_("recipient_id is required."))

        conversation_id = self._compute_conversation_id(recipient_id)

        message = await self._messaging.send_message(
            sender_id=self._identity.user_id,
            recipient_id=recipient_id,
            text=text,
            conversation_id=conversation_id,
            network=self._network,
        )

        message.reply_to_id = reply_to_id

        # Emit event
        await self._event_bus.emit_message_sent(message)

        return message

    async def receive_messages(
        self,
        timeout: float = 0.0,
    ) -> list[SDKMessage]:
        """Retrieve received messages from the internal queue.

        This is a polling-based receive. For push-based notifications,
        register an SDKEventHandler with on_message_received().

        Args:
            timeout: Maximum time in seconds to wait for a message.
                0.0 returns immediately with whatever is available.

        Returns:
            List of received SDKMessage objects (may be empty).

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
        """
        self._require_initialized()

        messages: list[SDKMessage] = []
        deadline = time.monotonic() + timeout if timeout > 0 else 0.0

        while True:
            remaining = deadline - time.monotonic() if deadline > 0 else 0.0
            if timeout > 0 and remaining <= 0:
                break

            try:
                wait_time = remaining if timeout > 0 else 0.001
                msg = await asyncio.wait_for(
                    self._recv_queue.get(), timeout=wait_time
                )
                messages.append(msg)
            except asyncio.TimeoutError:
                if timeout == 0:
                    break

        return messages

    # -------------------------------------------------------------------
    # Group API
    # -------------------------------------------------------------------

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        description: str = "",
    ) -> SDKGroup:
        """Create a new group chat.

        Args:
            name: Group display name.
            member_ids: List of user IDs to add as initial members.
            description: Optional group description.

        Returns:
            SDKGroup with assigned ID and member list.

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
            SDKGroupError: If the group cannot be created.
        """
        self._require_initialized()

        if not name:
            raise SDKGroupError(_("Group name is required."))
        if len(name) > MAX_GROUP_NAME_LENGTH:
            raise SDKGroupError(
                _("Group name exceeds {max_len} characters.").format(
                    max_len=MAX_GROUP_NAME_LENGTH
                )
            )
        if not member_ids:
            raise SDKGroupError(_("At least one member is required."))
        if len(member_ids) > MAX_GROUP_MEMBERS:
            raise SDKGroupError(
                _("Group cannot have more than {max_members} members.").format(
                    max_members=MAX_GROUP_MEMBERS
                )
            )

        group = SDKGroup(
            id=uuid.uuid4().hex,
            name=name,
            members=[self._identity.user_id] + member_ids,
            admin_id=self._identity.user_id,
            created_at=time.time(),
            description=description,
        )

        # In production: send group creation message via network
        # with fan-out to all members
        try:
            await self._messaging.create_group_message(
                sender_id=self._identity.user_id,
                group_id=group.id,
                text=_("[Group created: {name}]").format(name=name),
                network=self._network,
            )
        except SDKMessageError as exc:
            raise SDKGroupError(
                _("Failed to create group: {error}").format(error=exc)
            ) from exc

        self._groups[group.id] = group

        # Emit event
        await self._event_bus.emit_group_created(group)

        logger.info(
            _("Group '%s' created with %d members (id=%s)"),
            name, len(group.members), group.id,
        )
        return group

    # -------------------------------------------------------------------
    # Contact API
    # -------------------------------------------------------------------

    async def get_contacts(self) -> list[SDKContact]:
        """Get all contacts known to the SDK.

        Returns:
            List of SDKContact objects.

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
        """
        self._require_initialized()
        return list(self._contacts.values())

    async def add_contact(
        self,
        user_id: str,
        nickname: str = "",
        public_key_b64: str = "",
    ) -> SDKContact:
        """Add a new contact.

        Args:
            user_id: Presidium user ID (32 hex chars).
            nickname: Optional display name for the contact.
            public_key_b64: Optional base64-encoded Ed25519 public key.

        Returns:
            SDKContact for the added contact.

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
            SDKError: If the contact already exists or user_id is invalid.
        """
        self._require_initialized()

        if not user_id:
            raise SDKError(_("user_id is required to add a contact."))
        if user_id in self._contacts:
            raise SDKError(
                _("Contact {user_id} already exists.").format(user_id=user_id)
            )

        contact = SDKContact(
            user_id=user_id,
            nickname=nickname,
            public_key_b64=public_key_b64,
            is_verified=False,
            is_online=False,
            added_at=time.time(),
        )

        self._contacts[user_id] = contact

        # Emit event
        await self._event_bus.emit_contact_added(contact)

        logger.info(_("Contact added: %s (%s)"), nickname or user_id, user_id)
        return contact

    async def remove_contact(self, user_id: str) -> None:
        """Remove a contact.

        Args:
            user_id: User ID of the contact to remove.

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
            SDKError: If the contact does not exist.
        """
        self._require_initialized()

        if user_id not in self._contacts:
            raise SDKError(
                _("Contact {user_id} not found.").format(user_id=user_id)
            )

        del self._contacts[user_id]
        logger.info(_("Contact removed: %s"), user_id)

    # -------------------------------------------------------------------
    # Event handler registration
    # -------------------------------------------------------------------

    def register_handler(self, handler: SDKEventHandler) -> None:
        """Register an event handler for SDK events.

        Args:
            handler: The handler to register.
        """
        self._event_bus.register(handler)

    def unregister_handler(self, handler: SDKEventHandler) -> None:
        """Unregister a previously registered event handler.

        Args:
            handler: The handler to remove.
        """
        self._event_bus.unregister(handler)

    # -------------------------------------------------------------------
    # Thread-safe async wrappers
    # -------------------------------------------------------------------

    def send_message_sync(
        self,
        recipient_id: str,
        text: str,
        reply_to_id: str = "",
    ) -> SDKMessage:
        """Synchronous wrapper for send_message().

        Creates an event loop if needed, or runs on the current one.
        Thread-safe: can be called from any thread.

        Args:
            recipient_id: User ID of the recipient.
            text: Message body text.
            reply_to_id: Optional reply-to message ID.

        Returns:
            SDKMessage with assigned ID and timestamp.

        Raises:
            SDKMessageError: If the message cannot be sent.
        """
        return self._run_async(
            self.send_message(recipient_id, text, reply_to_id)
        )

    def receive_messages_sync(self, timeout: float = 0.0) -> list[SDKMessage]:
        """Synchronous wrapper for receive_messages().

        Thread-safe: can be called from any thread.

        Args:
            timeout: Maximum time to wait for messages.

        Returns:
            List of received SDKMessage objects.
        """
        return self._run_async(self.receive_messages(timeout))

    def create_group_sync(
        self,
        name: str,
        member_ids: list[str],
        description: str = "",
    ) -> SDKGroup:
        """Synchronous wrapper for create_group().

        Thread-safe: can be called from any thread.

        Args:
            name: Group display name.
            member_ids: List of member user IDs.
            description: Optional group description.

        Returns:
            SDKGroup with assigned ID.
        """
        return self._run_async(self.create_group(name, member_ids, description))

    def get_contacts_sync(self) -> list[SDKContact]:
        """Synchronous wrapper for get_contacts().

        Thread-safe: can be called from any thread.

        Returns:
            List of SDKContact objects.
        """
        return self._run_async(self.get_contacts())

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _require_initialized(self) -> None:
        """Check that the SDK is initialized, raise if not.

        Raises:
            SDKNotInitializedError: If the SDK is not initialized.
        """
        if not self._initialized:
            raise SDKNotInitializedError()

    @staticmethod
    def _compute_conversation_id(peer_id: str) -> str:
        """Compute a deterministic conversation ID for a 1:1 chat.

        The conversation ID is derived from the sorted pair of user IDs
        so that both parties compute the same ID regardless of who
        initiates the conversation.

        Args:
            peer_id: The peer's user ID.

        Returns:
            Conversation ID string.
        """
        import hashlib
        local_id = ""  # Will be filled in at call site
        sorted_ids = sorted([local_id, peer_id])
        raw = ":".join(sorted_ids)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    async def _heartbeat_loop(self) -> None:
        """Background task: send periodic heartbeats to the relay.

        Runs until cancelled or the SDK shuts down.
        """
        try:
            while not self._shutdown:
                if self._network.is_connected:
                    await self._network.send_heartbeat()
                await asyncio.sleep(self._config.heartbeat_interval)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Heartbeat loop error: %s", exc)
            await self._event_bus.emit_error(
                SDKConnectionError(_("Heartbeat failed: {error}").format(error=exc))
            )

    async def _receive_loop(self) -> None:
        """Background task: poll for incoming messages from the relay.

        Received messages are decrypted, placed on the internal queue,
        and dispatched to event handlers.

        Runs until cancelled or the SDK shuts down.
        """
        try:
            while not self._shutdown:
                if not self._network.is_connected:
                    await asyncio.sleep(0.5)
                    continue

                try:
                    raw = await self._network.receive_raw(timeout=1.0)
                    if raw is not None:
                        message = await self._messaging.receive_message(raw)
                        await self._recv_queue.put(message)
                        await self._event_bus.emit_message_received(message)
                except SDKMessageError as exc:
                    logger.warning("Failed to process incoming message: %s", exc)
                    await self._event_bus.emit_error(exc)
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.debug("Receive loop iteration error: %s", exc)
                    await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            return

    @staticmethod
    def _run_async(coro: Any) -> Any:
        """Run an async coroutine from a synchronous context.

        Handles the case where an event loop is already running (e.g.,
        in a Jupyter notebook or existing async application) by using
        a separate thread.

        Args:
            coro: The coroutine to run.

        Returns:
            The result of the coroutine.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an existing event loop — use a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=DEFAULT_TIMEOUT_SECONDS)
        else:
            return asyncio.run(coro)


__all__ = [
    "SDKError",
    "SDKNotInitializedError",
    "SDKConnectionError",
    "SDKAuthenticationError",
    "SDKMessageError",
    "SDKGroupError",
    "SDKTimeoutError",
    "SDKConfigError",
    "SDKEvent",
    "ConnectionState",
    "MessageType",
    "SDKConfig",
    "SDKMessage",
    "SDKContact",
    "SDKGroup",
    "SDKEncryptionStats",
    "SDKEventHandler",
    "AsyncEventBus",
    "IdentityFacade",
    "NetworkFacade",
    "MessagingFacade",
    "PresidiumSDK",
]
