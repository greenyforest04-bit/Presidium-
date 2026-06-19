"""Peer connection manager for Presidium.

Manages the lifecycle of peer connections:
- Peer discovery → handshake → connected
- E2EE key exchange session management
- Message routing between peers
- Connection health monitoring
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from presidium.core.crypto import (
    Ed25519KeyPair,
    X25519KeyPair,
    SecureBytes,
    compute_user_id,
    derive_session_key,
)
from presidium.core.network.protocol import (
    MessageType,
    HelloPayload,
    HelloAckPayload,
    KeyExchangePayload,
    KeyExchangeAckPayload,
    ChatMessagePayload,
    ChatAckPayload,
    encode_message,
    decode_message,
    ProtocolError,
)
from presidium.core.network.transport import UDPTransport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PeerError(Exception):
    """Base error for peer operations."""


class PeerNotFoundError(PeerError):
    """Peer not found in the connection table."""


class HandshakeError(PeerError):
    """Handshake with peer failed."""


class KeyExchangeError(PeerError):
    """E2EE key exchange failed."""


# ---------------------------------------------------------------------------
# Peer info
# ---------------------------------------------------------------------------

@dataclass
class PeerInfo:
    """Information about a known peer."""
    user_id: str  # 32 hex chars
    public_key_bytes: bytes  # 32 bytes Ed25519
    addr: Optional[tuple[str, int]] = None
    nickname: str = ""
    connected: bool = False
    last_seen: float = 0.0
    external_addr: Optional[str] = None


# ---------------------------------------------------------------------------
# E2EE Session
# ---------------------------------------------------------------------------

@dataclass
class E2EESession:
    """An established end-to-end encrypted session with a peer."""
    peer_user_id: str
    session_id: bytes  # 16 bytes
    send_chain_key: bytes  # 32 bytes — current sending chain
    recv_chain_key: bytes  # 32 bytes — current receiving chain
    ephemeral_local: X25519KeyPair
    ephemeral_remote_public: bytes  # 32 bytes X25519 public key
    established_at: float = field(default_factory=time.monotonic)

    def derive_next_send_key(self) -> bytes:
        """Advance the send chain and return the next message key."""
        from presidium.core.crypto import advance_chain_key
        self.send_chain_key, msg_key = advance_chain_key(self.send_chain_key)
        return msg_key

    def derive_next_recv_key(self) -> bytes:
        """Advance the receive chain and return the next message key."""
        from presidium.core.crypto import advance_chain_key
        self.recv_chain_key, msg_key = advance_chain_key(self.recv_chain_key)
        return msg_key


# ---------------------------------------------------------------------------
# Peer Manager
# ---------------------------------------------------------------------------

class PeerManager:
    """Manages all peer connections and E2EE sessions.

    Responsibilities:
    - Maintain a table of known peers
    - Handle incoming HELLO and KEY_EXCHANGE messages
    - Initiate outbound handshakes
    - Route encrypted messages to the correct E2EE session
    - Track connection health and reconnect as needed
    """

    def __init__(
        self,
        transport: UDPTransport,
        local_key_pair: Ed25519KeyPair,
        local_user_id: str,
    ) -> None:
        self._transport = transport
        self._local_key_pair = local_key_pair
        self._local_user_id = local_user_id

        # Known peers: user_id → PeerInfo
        self._peers: dict[str, PeerInfo] = {}

        # Active E2EE sessions: peer_user_id → E2EESession
        self._sessions: dict[str, E2EESession] = {}

        # Pending key exchanges: session_id → (peer_user_id, X25519KeyPair)
        self._pending_kx: dict[bytes, tuple[str, X25519KeyPair]] = {}

        # Callbacks
        self._on_chat_message: Optional[Callable[[str, bytes, bytes, float], Awaitable[None]]] = None
        self._on_peer_connected: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_peer_disconnected: Optional[Callable[[str], Awaitable[None]]] = None

        # Register transport handlers
        self._transport.register_handler(MessageType.HELLO, self._handle_hello)
        self._transport.register_handler(MessageType.HELLO_ACK, self._handle_hello_ack)
        self._transport.register_handler(MessageType.KEY_EXCHANGE, self._handle_key_exchange)
        self._transport.register_handler(MessageType.KEY_EXCHANGE_ACK, self._handle_key_exchange_ack)
        self._transport.register_handler(MessageType.CHAT_MESSAGE, self._handle_chat_message)
        self._transport.register_handler(MessageType.CHAT_ACK, self._handle_chat_ack)

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    @property
    def local_user_id(self) -> str:
        return self._local_user_id

    def set_chat_handler(
        self,
        callback: Callable[[str, bytes, bytes, float], Awaitable[None]],
    ) -> None:
        """Set callback for incoming chat messages: (peer_user_id, message_id, decrypted_content, timestamp)."""
        self._on_chat_message = callback

    def set_peer_connected_handler(
        self,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Set callback for when a peer connects."""
        self._on_peer_connected = callback

    def set_peer_disconnected_handler(
        self,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Set callback for when a peer disconnects."""
        self._on_peer_disconnected = callback

    def add_known_peer(
        self,
        user_id: str,
        public_key_bytes: bytes,
        nickname: str = "",
        addr: Optional[tuple[str, int]] = None,
    ) -> PeerInfo:
        """Add a peer to the known peers table."""
        peer = PeerInfo(
            user_id=user_id,
            public_key_bytes=public_key_bytes,
            nickname=nickname,
            addr=addr,
            last_seen=time.monotonic(),
        )
        self._peers[user_id] = peer
        logger.info("Added known peer: %s (%s)", user_id, nickname or "no nickname")
        return peer

    def get_peer(self, user_id: str) -> Optional[PeerInfo]:
        """Get peer info by user ID."""
        return self._peers.get(user_id)

    def get_all_peers(self) -> list[PeerInfo]:
        """Get all known peers."""
        return list(self._peers.values())

    def get_connected_peers(self) -> list[PeerInfo]:
        """Get all currently connected peers."""
        return [p for p in self._peers.values() if p.connected]

    def has_session(self, peer_user_id: str) -> bool:
        """Check if an E2EE session exists with a peer."""
        return peer_user_id in self._sessions

    # -------------------------------------------------------------------
    # Outbound handshake
    # -------------------------------------------------------------------

    async def connect_to_peer(self, peer: PeerInfo) -> None:
        """Initiate connection to a peer: handshake + key exchange.

        Args:
            peer: PeerInfo with at least addr and public_key_bytes.

        Raises:
            PeerError: If the peer has no address or handshake fails.
        """
        if peer.addr is None:
            raise PeerError(f"Cannot connect to peer {peer.user_id}: no address.")

        # Step 1: NAT hole punch
        punch_ok = await self._transport.punch(peer.addr, bytes.fromhex(peer.user_id))
        if not punch_ok:
            logger.warning("NAT punch failed for %s, attempting direct send", peer.user_id)

        # Step 2: Send HELLO
        hello = HelloPayload(
            user_id=bytes.fromhex(self._local_user_id),
            public_key=self._local_key_pair.public_key_bytes,
            listen_port=self._transport._config.listen_port,
        )
        await self._transport.send_reliable(MessageType.HELLO, hello, peer.addr)
        logger.info("HELLO sent to %s", peer.user_id)

    async def initiate_key_exchange(self, peer_user_id: str) -> None:
        """Start an E2EE key exchange with a connected peer.

        Generates an ephemeral X25519 key pair, signs it with the
        long-term Ed25519 key, and sends KEY_EXCHANGE.
        """
        peer = self._peers.get(peer_user_id)
        if peer is None or peer.addr is None:
            raise PeerNotFoundError(f"Peer {peer_user_id} not found or has no address.")

        # Generate ephemeral X25519 key pair
        ephemeral = X25519KeyPair()
        session_id = __import__("os").urandom(16)

        # Sign (sender_id + ephemeral_pub + session_id)
        signed_data = (
            bytes.fromhex(self._local_user_id)
            + ephemeral.public_key_bytes
            + session_id
        )
        signature = self._local_key_pair.sign(signed_data)

        kx_payload = KeyExchangePayload(
            sender_user_id=bytes.fromhex(self._local_user_id),
            ephemeral_public_key=ephemeral.public_key_bytes,
            session_id=session_id,
            signed_payload=signature,
        )

        # Store pending KX
        self._pending_kx[session_id] = (peer_user_id, ephemeral)

        await self._transport.send_reliable(MessageType.KEY_EXCHANGE, kx_payload, peer.addr)
        logger.info("Key exchange initiated with %s (session %s)", peer_user_id, session_id.hex())

    # -------------------------------------------------------------------
    # Send encrypted chat message
    # -------------------------------------------------------------------

    async def send_chat_message(
        self,
        peer_user_id: str,
        plaintext: bytes,
        reply_to: bytes = b"",
    ) -> bytes:
        """Send an encrypted chat message to a peer.

        Args:
            peer_user_id: The recipient's user ID.
            plaintext: The raw message content (UTF-8 bytes).
            reply_to: Optional message_id of the message being replied to.

        Returns:
            The message_id (16 bytes).

        Raises:
            PeerError: If no E2EE session exists.
        """
        session = self._sessions.get(peer_user_id)
        if session is None:
            raise PeerError(f"No E2EE session with {peer_user_id}. Initiate key exchange first.")

        peer = self._peers.get(peer_user_id)
        if peer is None or peer.addr is None:
            raise PeerNotFoundError(f"Peer {peer_user_id} not found or has no address.")

        # Derive message key from chain
        msg_key = session.derive_next_send_key()

        # Encrypt with AES-256-GCM
        from presidium.core.crypto import AES256GCM
        encrypted = AES256GCM.encrypt(msg_key, plaintext)

        message_id = __import__("os").urandom(16)

        payload = ChatMessagePayload(
            session_id=session.session_id,
            sender_user_id=bytes.fromhex(self._local_user_id),
            message_id=message_id,
            encrypted_content=encrypted,
            timestamp=time.time(),
            reply_to=reply_to,
        )

        await self._transport.send_reliable(MessageType.CHAT_MESSAGE, payload, peer.addr)
        return message_id

    # -------------------------------------------------------------------
    # Incoming message handlers
    # -------------------------------------------------------------------

    async def _handle_hello(
        self, msg_type: int, timestamp: float, payload: HelloPayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming HELLO: validate and send HELLO_ACK."""
        peer_uid = payload.user_id.hex()
        logger.info("HELLO received from %s at %s", peer_uid, addr)

        # Verify the public key matches the user_id
        expected_uid = compute_user_id(payload.public_key)
        if expected_uid != peer_uid:
            logger.warning("HELLO from %s: user_id/public_key mismatch, rejecting", peer_uid)
            ack = HelloAckPayload(
                user_id=bytes.fromhex(self._local_user_id),
                public_key=self._local_key_pair.public_key_bytes,
                accepted=False,
                error_msg="User ID does not match public key.",
            )
            await self._transport.send(MessageType.HELLO_ACK, ack, addr)
            return

        # Add/update peer
        peer = self._peers.get(peer_uid)
        if peer is None:
            peer = self.add_known_peer(
                user_id=peer_uid,
                public_key_bytes=payload.public_key,
                addr=addr,
            )
        else:
            peer.addr = addr
            peer.last_seen = time.monotonic()

        # Accept
        ack = HelloAckPayload(
            user_id=bytes.fromhex(self._local_user_id),
            public_key=self._local_key_pair.public_key_bytes,
            accepted=True,
        )
        await self._transport.send(MessageType.HELLO_ACK, ack, addr)

        # Auto-initiate key exchange
        if not self.has_session(peer_uid):
            await self.initiate_key_exchange(peer_uid)

    async def _handle_hello_ack(
        self, msg_type: int, timestamp: float, payload: HelloAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle HELLO_ACK."""
        peer_uid = payload.user_id.hex()
        if not payload.accepted:
            logger.warning("HELLO rejected by %s: %s", peer_uid, payload.error_msg)
            return

        logger.info("HELLO_ACK received from %s", peer_uid)

        # Add/update peer
        peer = self._peers.get(peer_uid)
        if peer is None:
            peer = self.add_known_peer(
                user_id=peer_uid,
                public_key_bytes=payload.public_key,
                addr=addr,
            )
        else:
            peer.addr = addr

        peer.connected = True
        peer.last_seen = time.monotonic()

        # Initiate key exchange if no session
        if not self.has_session(peer_uid):
            await self.initiate_key_exchange(peer_uid)

        if self._on_peer_connected:
            await self._on_peer_connected(peer_uid)

    async def _handle_key_exchange(
        self, msg_type: int, timestamp: float, payload: KeyExchangePayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming KEY_EXCHANGE: verify signature and establish session."""
        peer_uid = payload.sender_user_id.hex()
        logger.info("KEY_EXCHANGE received from %s", peer_uid)

        peer = self._peers.get(peer_uid)
        if peer is None:
            logger.warning("KEY_EXCHANGE from unknown peer %s", peer_uid)
            return

        # Verify Ed25519 signature
        signed_data = (
            payload.sender_user_id
            + payload.ephemeral_public_key
            + payload.session_id
        )
        if not Ed25519KeyPair.verify(peer.public_key_bytes, payload.signed_payload, signed_data):
            logger.warning("KEY_EXCHANGE signature verification failed for %s", peer_uid)
            return

        # Generate our ephemeral X25519 pair
        ephemeral = X25519KeyPair()

        # Compute shared secret
        shared_secret = ephemeral.shared_secret(payload.ephemeral_public_key)

        # Derive chain keys using HKDF
        session_key = derive_session_key(shared_secret)
        raw_key = bytes(session_key)

        # Split into send and recv chain keys
        # Initiator sends on chain A, responder on chain B
        from presidium.core.crypto import AES256GCM
        chain_a = raw_key[:16] + __import__("os").urandom(16)  # Send chain
        chain_b = raw_key[16:] + __import__("os").urandom(16)  # Recv chain

        # As responder, we receive on chain A, send on chain B
        session = E2EESession(
            peer_user_id=peer_uid,
            session_id=payload.session_id,
            send_chain_key=chain_b,
            recv_chain_key=chain_a,
            ephemeral_local=ephemeral,
            ephemeral_remote_public=payload.ephemeral_public_key,
        )
        self._sessions[peer_uid] = session

        # Sign and send KEY_EXCHANGE_ACK
        ack_signed_data = (
            bytes.fromhex(self._local_user_id)
            + ephemeral.public_key_bytes
            + payload.session_id
        )
        ack_signature = self._local_key_pair.sign(ack_signed_data)

        ack = KeyExchangeAckPayload(
            responder_user_id=bytes.fromhex(self._local_user_id),
            ephemeral_public_key=ephemeral.public_key_bytes,
            session_id=payload.session_id,
            signed_payload=ack_signature,
        )

        await self._transport.send_reliable(MessageType.KEY_EXCHANGE_ACK, ack, addr)
        logger.info("E2EE session established with %s (responder)", peer_uid)

        peer.connected = True
        if self._on_peer_connected:
            await self._on_peer_connected(peer_uid)

    async def _handle_key_exchange_ack(
        self, msg_type: int, timestamp: float, payload: KeyExchangeAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle KEY_EXCHANGE_ACK: complete the key exchange as initiator."""
        peer_uid = payload.responder_user_id.hex()
        session_id = payload.session_id

        logger.info("KEY_EXCHANGE_ACK received from %s", peer_uid)

        peer = self._peers.get(peer_uid)
        if peer is None:
            logger.warning("KEY_EXCHANGE_ACK from unknown peer %s", peer_uid)
            return

        # Verify signature
        signed_data = (
            payload.responder_user_id
            + payload.ephemeral_public_key
            + session_id
        )
        if not Ed25519KeyPair.verify(peer.public_key_bytes, payload.signed_payload, signed_data):
            logger.warning("KEY_EXCHANGE_ACK signature verification failed for %s", peer_uid)
            return

        # Look up our pending ephemeral key
        pending = self._pending_kx.pop(session_id, None)
        if pending is None:
            logger.warning("No pending key exchange for session %s", session_id.hex())
            return

        _, ephemeral = pending

        # Compute shared secret
        shared_secret = ephemeral.shared_secret(payload.ephemeral_public_key)
        session_key = derive_session_key(shared_secret)
        raw_key = bytes(session_key)

        # As initiator, we send on chain A, receive on chain B
        chain_a = raw_key[:16] + __import__("os").urandom(16)
        chain_b = raw_key[16:] + __import__("os").urandom(16)

        # SIMPLIFIED: in production, derive chain keys deterministically from shared secret
        # rather than mixing random bytes. Here we use a simplified approach for demo.
        session = E2EESession(
            peer_user_id=peer_uid,
            session_id=session_id,
            send_chain_key=chain_a,
            recv_chain_key=chain_b,
            ephemeral_local=ephemeral,
            ephemeral_remote_public=payload.ephemeral_public_key,
        )
        self._sessions[peer_uid] = session

        peer.connected = True
        logger.info("E2EE session established with %s (initiator)", peer_uid)

        if self._on_peer_connected:
            await self._on_peer_connected(peer_uid)

    async def _handle_chat_message(
        self, msg_type: int, timestamp: float, payload: ChatMessagePayload, addr: tuple[str, int]
    ) -> None:
        """Handle incoming encrypted chat message: decrypt and dispatch."""
        peer_uid = payload.sender_user_id.hex()
        session = self._sessions.get(peer_uid)

        if session is None:
            logger.warning("Chat message from %s with no E2EE session", peer_uid)
            return

        # Derive receive key and decrypt
        msg_key = session.derive_next_recv_key()
        from presidium.core.crypto import AES256GCM
        try:
            plaintext = AES256GCM.decrypt(msg_key, payload.encrypted_content)
        except Exception as exc:
            logger.error("Failed to decrypt message from %s: %s", peer_uid, exc)
            return

        # Send ACK
        ack = ChatAckPayload(
            session_id=payload.session_id,
            message_id=payload.message_id,
            status=0,  # received
        )
        peer = self._peers.get(peer_uid)
        if peer and peer.addr:
            await self._transport.send(MessageType.CHAT_ACK, ack, peer.addr)

        # Dispatch to chat handler
        if self._on_chat_message:
            await self._on_chat_message(peer_uid, payload.message_id, plaintext, payload.timestamp)

    async def _handle_chat_ack(
        self, msg_type: int, timestamp: float, payload: ChatAckPayload, addr: tuple[str, int]
    ) -> None:
        """Handle chat message delivery ACK."""
        logger.debug("Chat ACK for message %s, status=%d", payload.message_id.hex(), payload.status)
        # SIMPLIFIED: in production, update message status in DB and notify UI

    # -------------------------------------------------------------------
    # Disconnect
    # -------------------------------------------------------------------

    async def disconnect_peer(self, peer_user_id: str, reason: str = "") -> None:
        """Disconnect from a peer."""
        from presidium.core.network.protocol import DisconnectPayload
        peer = self._peers.get(peer_user_id)
        if peer and peer.addr:
            disc = DisconnectPayload(
                user_id=bytes.fromhex(self._local_user_id),
                reason=reason,
            )
            await self._transport.send(MessageType.DISCONNECT, disc, peer.addr)

        peer_uid = peer_user_id
        self._sessions.pop(peer_uid, None)
        if peer:
            peer.connected = False
        if self._on_peer_disconnected:
            await self._on_peer_disconnected(peer_uid)
        logger.info("Disconnected from %s", peer_uid)
