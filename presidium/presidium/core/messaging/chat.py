"""Chat session management and E2EE handshake for Presidium.

Provides:
- ChatSession: manages a conversation with a single peer
- Message status tracking (sent, delivered, read, error)
- Full Double Ratchet integration for E2EE
- Message persistence callbacks for the database layer
- Typing indicator support
- Reply/forward message references
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.crypto import AES256GCM, CryptoError, SecureBytes
from presidium.core.messaging.ratchet import (
    DoubleRatchet,
    RatchetError,
    RatchetNotInitializedError,
    MessageAuthenticationError,
    encrypt_header,
    decrypt_header,
    RatchetHeader,
    encode_header,
)
from presidium.core.network.peer import PeerManager, PeerError, PeerNotFoundError
from presidium.core.network.protocol import (
    MessageType,
    ChatMessagePayload,
    ChatAckPayload,
    ChatReadPayload,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ChatError(Exception):
    """Base chat error."""


class SessionNotFoundError(ChatError):
    """Chat session not found."""


class MessageSendError(ChatError):
    """Failed to send a message."""


class DecryptionFailedError(ChatError):
    """Failed to decrypt an incoming message."""


# ---------------------------------------------------------------------------
# Message status
# ---------------------------------------------------------------------------

class MessageStatus(enum.IntEnum):
    """Delivery status of a chat message."""
    SENDING = 0
    SENT = 1
    DELIVERED = 2
    READ = 3
    ERROR = 4


# ---------------------------------------------------------------------------
# Message model
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    """A single chat message."""
    message_id: bytes  # 16 bytes
    sender_user_id: str
    recipient_user_id: str
    content: str  # UTF-8 decoded plaintext
    timestamp: float
    status: MessageStatus = MessageStatus.SENDING
    reply_to: bytes = b""  # message_id of the message being replied to
    is_mine: bool = False  # True if sent by local user

    @property
    def message_id_hex(self) -> str:
        return self.message_id.hex()

    def to_dict(self) -> dict:
        """Serialize to a dict for database storage."""
        return {
            "message_id": self.message_id_hex,
            "sender_user_id": self.sender_user_id,
            "recipient_user_id": self.recipient_user_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "status": int(self.status),
            "reply_to": self.reply_to.hex() if self.reply_to else "",
            "is_mine": self.is_mine,
        }


# ---------------------------------------------------------------------------
# Chat Session
# ---------------------------------------------------------------------------

@dataclass
class ChatSession:
    """Manages a conversation with a single peer.

    Tracks:
    - Peer identity (user_id)
    - Message history (in-memory, synced to DB)
    - Unread count
    - Last message preview
    - Double Ratchet state for E2EE
    - Typing indicator state
    """
    peer_user_id: str
    peer_nickname: str = ""
    peer_public_key: bytes = b""
    messages: list[ChatMessage] = field(default_factory=list)
    unread_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_message_preview: str = ""
    last_message_timestamp: float = 0.0
    ratchet: Optional[DoubleRatchet] = None
    is_typing: bool = False  # whether the peer is currently typing

    @property
    def display_name(self) -> str:
        """Return the best available display name for the peer."""
        return self.peer_nickname or self.peer_user_id[:8]

    @property
    def has_ratchet(self) -> bool:
        """Check if an E2EE ratchet session is established."""
        return self.ratchet is not None and self.ratchet.is_initialized

    def add_message(self, message: ChatMessage) -> None:
        """Add a message to the session and update preview."""
        self.messages.append(message)
        self.last_message_preview = message.content[:80] if message.content else ""
        self.last_message_timestamp = message.timestamp
        if not message.is_mine:
            self.unread_count += 1

    def mark_all_read(self) -> None:
        """Mark all messages as read."""
        self.unread_count = 0
        for msg in self.messages:
            if not msg.is_mine and msg.status != MessageStatus.READ:
                msg.status = MessageStatus.READ


# ---------------------------------------------------------------------------
# Chat Manager
# ---------------------------------------------------------------------------

class ChatManager:
    """Manages all chat sessions and message routing.

    Integrates with PeerManager for sending and receiving encrypted messages,
    DoubleRatchet for E2EE, and the database layer for persistence.
    """

    def __init__(self, peer_manager: PeerManager, local_user_id: str) -> None:
        self._peer_manager = peer_manager
        self._local_user_id = local_user_id

        # Active chat sessions: peer_user_id → ChatSession
        self._sessions: dict[str, ChatSession] = {}

        # Callbacks
        self._on_message_received: Optional[Callable[[ChatMessage], Awaitable[None]]] = None
        self._on_message_status_changed: Optional[Callable[[bytes, MessageStatus], Awaitable[None]]] = None
        self._on_session_updated: Optional[Callable[[ChatSession], Awaitable[None]]] = None
        self._on_typing_indicator: Optional[Callable[[str, bool], Awaitable[None]]] = None
        self._persist_message: Optional[Callable[[ChatMessage], Awaitable[None]]] = None
        self._persist_ratchet: Optional[Callable[[str, DoubleRatchet], Awaitable[None]]] = None
        self._load_messages: Optional[Callable[[str], Awaitable[list[ChatMessage]]]] = None

        # Register with peer manager
        self._peer_manager.set_chat_handler(self._on_incoming_message)

    # -------------------------------------------------------------------
    # Callback setters
    # -------------------------------------------------------------------

    def set_on_message_received(
        self, callback: Callable[[ChatMessage], Awaitable[None]]
    ) -> None:
        self._on_message_received = callback

    def set_on_message_status_changed(
        self, callback: Callable[[bytes, MessageStatus], Awaitable[None]]
    ) -> None:
        self._on_message_status_changed = callback

    def set_on_session_updated(
        self, callback: Callable[[ChatSession], Awaitable[None]]
    ) -> None:
        self._on_session_updated = callback

    def set_on_typing_indicator(
        self, callback: Callable[[str, bool], Awaitable[None]]
    ) -> None:
        self._on_typing_indicator = callback

    def set_persist_message(
        self, callback: Callable[[ChatMessage], Awaitable[None]]
    ) -> None:
        self._persist_message = callback

    def set_persist_ratchet(
        self, callback: Callable[[str, DoubleRatchet], Awaitable[None]]
    ) -> None:
        self._persist_ratchet = callback

    def set_load_messages(
        self, callback: Callable[[str], Awaitable[list[ChatMessage]]]
    ) -> None:
        self._load_messages = callback

    # -------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------

    def get_or_create_session(self, peer_user_id: str, nickname: str = "") -> ChatSession:
        """Get an existing session or create a new one for a peer."""
        if peer_user_id not in self._sessions:
            peer = self._peer_manager.get_peer(peer_user_id)
            session = ChatSession(
                peer_user_id=peer_user_id,
                peer_nickname=nickname or (peer.nickname if peer else ""),
                peer_public_key=peer.public_key_bytes if peer else b"",
            )
            self._sessions[peer_user_id] = session

            if self._load_messages:
                asyncio.ensure_future(self._load_history(peer_user_id))

        return self._sessions[peer_user_id]

    def get_session(self, peer_user_id: str) -> Optional[ChatSession]:
        """Get a session by peer user ID."""
        return self._sessions.get(peer_user_id)

    def get_all_sessions(self) -> list[ChatSession]:
        """Get all sessions sorted by last message timestamp (newest first)."""
        sessions = list(self._sessions.values())
        sessions.sort(key=lambda s: s.last_message_timestamp, reverse=True)
        return sessions

    # -------------------------------------------------------------------
    # Ratchet initialization
    # -------------------------------------------------------------------

    def init_ratchet_as_initiator(
        self,
        peer_user_id: str,
        shared_secret: bytes,
        remote_dh_public: bytes,
    ) -> bytes:
        """Initialize the Double Ratchet as initiator for a session.

        Args:
            peer_user_id: The peer's user ID.
            shared_secret: The X3DH shared secret.
            remote_dh_public: Peer's X25519 prekey (32 bytes).

        Returns:
            Our initial DH public key to send to the peer.
        """
        session = self.get_or_create_session(peer_user_id)
        ratchet = DoubleRatchet()
        our_public = ratchet.init_as_initiator(shared_secret, remote_dh_public)
        session.ratchet = ratchet

        if self._persist_ratchet:
            asyncio.ensure_future(self._persist_ratchet(peer_user_id, ratchet))

        logger.info("Ratchet initialized as initiator for %s", peer_user_id[:8])
        return our_public

    def init_ratchet_as_responder(
        self,
        peer_user_id: str,
        shared_secret: bytes,
        remote_dh_public: bytes,
    ) -> None:
        """Initialize the Double Ratchet as responder for a session.

        Args:
            peer_user_id: The peer's user ID.
            shared_secret: The X3DH shared secret.
            remote_dh_public: Initiator's first DH ratchet public key.
        """
        session = self.get_or_create_session(peer_user_id)
        ratchet = DoubleRatchet()
        ratchet.init_as_responder(shared_secret, remote_dh_public)
        session.ratchet = ratchet

        if self._persist_ratchet:
            asyncio.ensure_future(self._persist_ratchet(peer_user_id, ratchet))

        logger.info("Ratchet initialized as responder for %s", peer_user_id[:8])

    # -------------------------------------------------------------------
    # Sending messages
    # -------------------------------------------------------------------

    async def send_message(
        self,
        peer_user_id: str,
        text: str,
        reply_to: bytes = b"",
    ) -> ChatMessage:
        """Send an encrypted chat message to a peer.

        Uses the Double Ratchet to encrypt the message. If no ratchet
        session exists, falls back to the PeerManager's legacy encryption.

        Args:
            peer_user_id: The recipient's user ID.
            text: Message text content.
            reply_to: Optional message_id being replied to.

        Returns:
            The ChatMessage that was sent.

        Raises:
            MessageSendError: If sending fails.
        """
        from presidium.utils.validators import validate_message_text
        validate_message_text(text)

        session = self.get_or_create_session(peer_user_id)
        message_id = uuid.uuid4().bytes

        message = ChatMessage(
            message_id=message_id,
            sender_user_id=self._local_user_id,
            recipient_user_id=peer_user_id,
            content=text,
            timestamp=time.time(),
            status=MessageStatus.SENDING,
            reply_to=reply_to,
            is_mine=True,
        )

        session.add_message(message)

        try:
            if session.has_ratchet:
                # Use Double Ratchet encryption
                encrypted_header, encrypted_body = session.ratchet.encrypt(
                    text.encode("utf-8")
                )
                # Combine header and body for transport
                combined = (
                    len(encrypted_header).to_bytes(4, "big")
                    + encrypted_header
                    + encrypted_body
                )
                await self._peer_manager.send_chat_message(
                    peer_user_id,
                    combined,
                    reply_to=reply_to,
                )
            else:
                # Fallback: use PeerManager's legacy E2EE
                await self._peer_manager.send_chat_message(
                    peer_user_id,
                    text.encode("utf-8"),
                    reply_to=reply_to,
                )

            message.status = MessageStatus.SENT

            # Persist ratchet state after encryption
            if self._persist_ratchet and session.ratchet:
                await self._persist_ratchet(peer_user_id, session.ratchet)

        except (PeerError, RatchetError, CryptoError) as exc:
            message.status = MessageStatus.ERROR
            logger.error("Failed to send message to %s: %s", peer_user_id, exc)
            raise MessageSendError(f"Failed to send message: {exc}") from exc

        # Persist message
        if self._persist_message:
            try:
                await self._persist_message(message)
            except Exception as exc:
                logger.error("Failed to persist message: %s", exc)

        # Notify UI
        if self._on_message_status_changed:
            await self._on_message_status_changed(message_id, message.status)
        if self._on_session_updated:
            await self._on_session_updated(session)

        return message

    # -------------------------------------------------------------------
    # Receiving messages
    # -------------------------------------------------------------------

    async def _on_incoming_message(
        self,
        peer_user_id: str,
        message_id: bytes,
        plaintext: bytes,
        timestamp: float,
    ) -> None:
        """Callback from PeerManager when an encrypted message is received.

        The plaintext may be either:
        - A Double Ratchet encrypted message (prefixed with 4-byte header length)
        - A legacy AES-GCM encrypted message (no prefix)
        """
        session = self.get_or_create_session(peer_user_id)

        # Try Double Ratchet decryption if we have a ratchet session
        decrypted: bytes
        if session.has_ratchet and len(plaintext) > 4:
            header_len = int.from_bytes(plaintext[:4], "big")
            if header_len > 0 and header_len < len(plaintext) - 4:
                encrypted_header = plaintext[4 : 4 + header_len]
                encrypted_body = plaintext[4 + header_len :]
                try:
                    decrypted = session.ratchet.decrypt(encrypted_header, encrypted_body)
                    # Persist ratchet state after decryption
                    if self._persist_ratchet and session.ratchet:
                        await self._persist_ratchet(peer_user_id, session.ratchet)
                except MessageAuthenticationError as exc:
                    logger.error("Message auth failed from %s: %s", peer_user_id[:8], exc)
                    return
                except RatchetError as exc:
                    logger.error("Ratchet error from %s: %s", peer_user_id[:8], exc)
                    # Fall back to treating plaintext as already decrypted
                    decrypted = plaintext
            else:
                decrypted = plaintext
        else:
            decrypted = plaintext

        try:
            content = decrypted.decode("utf-8")
        except UnicodeDecodeError:
            content = f"<binary data: {len(decrypted)} bytes>"

        message = ChatMessage(
            message_id=message_id,
            sender_user_id=peer_user_id,
            recipient_user_id=self._local_user_id,
            content=content,
            timestamp=timestamp,
            status=MessageStatus.DELIVERED,
            is_mine=False,
        )

        session.add_message(message)

        # Persist
        if self._persist_message:
            try:
                await self._persist_message(message)
            except Exception as exc:
                logger.error("Failed to persist received message: %s", exc)

        # Notify UI
        if self._on_message_received:
            await self._on_message_received(message)
        if self._on_session_updated:
            await self._on_session_updated(session)

        logger.debug("Received message from %s: %s", peer_user_id[:8], content[:50])

    # -------------------------------------------------------------------
    # Message status updates
    # -------------------------------------------------------------------

    async def mark_as_read(self, peer_user_id: str) -> None:
        """Mark all unread messages in a session as read."""
        session = self._sessions.get(peer_user_id)
        if session is None:
            return

        session.mark_all_read()

        # Send READ notifications
        message_ids = [
            msg.message_id for msg in session.messages
            if not msg.is_mine and msg.status != MessageStatus.READ
        ]
        if message_ids and self._peer_manager.has_session(peer_user_id):
            peer = self._peer_manager.get_peer(peer_user_id)
            if peer and peer.addr:
                read_payload = ChatReadPayload(
                    session_id=b"",
                    message_ids=message_ids,
                )
                try:
                    from presidium.core.network.transport import UDPTransport
                    # Access transport through peer manager
                    await self._peer_manager._transport.send(
                        MessageType.CHAT_READ, read_payload, peer.addr
                    )
                except Exception as exc:
                    logger.debug("Failed to send READ notification: %s", exc)

        # Update statuses locally
        for msg in session.messages:
            if not msg.is_mine and msg.status != MessageStatus.READ:
                msg.status = MessageStatus.READ
                if self._on_message_status_changed:
                    await self._on_message_status_changed(msg.message_id, MessageStatus.READ)

        if self._on_session_updated:
            await self._on_session_updated(session)

    # -------------------------------------------------------------------
    # Typing indicators
    # -------------------------------------------------------------------

    async def send_typing(self, peer_user_id: str, is_typing: bool) -> None:
        """Send a typing indicator to a peer."""
        from presidium.core.network.protocol import (
            TypingIndicatorPayload,
        )
        # PHASE2: TypingIndicator is a new protocol message type
        # For now we use a simple approach
        peer = self._peer_manager.get_peer(peer_user_id)
        if not peer or not peer.addr:
            return

        # SIMPLIFIED: actual typing indicator protocol message
        # Would need to add TYPING_START/TYPING_STOP to protocol.py
        pass

    # -------------------------------------------------------------------
    # History loading
    # -------------------------------------------------------------------

    async def _load_history(self, peer_user_id: str) -> None:
        """Load message history from database for a session."""
        if self._load_messages is None:
            return
        try:
            messages = await self._load_messages(peer_user_id)
            session = self._sessions.get(peer_user_id)
            if session and not session.messages:
                session.messages = messages
                if messages:
                    last = messages[-1]
                    session.last_message_preview = last.content[:80]
                    session.last_message_timestamp = last.timestamp
        except Exception as exc:
            logger.error("Failed to load message history for %s: %s", peer_user_id, exc)
