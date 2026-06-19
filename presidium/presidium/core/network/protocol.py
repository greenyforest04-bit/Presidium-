"""Binary message protocol for Presidium P2P network.

Uses msgspec for efficient binary serialization with schema evolution support.
All network messages follow a common envelope format:

    [4 bytes: magic] [1 byte: version] [1 byte: msg_type]
    [8 bytes: timestamp] [4 bytes: payload_length]
    [N bytes: payload (msgspec-encoded)]

The protocol supports:
- Handshake (key exchange, NAT discovery)
- Chat messages (encrypted)
- DHT operations (FIND_NODE, FIND_VALUE, STORE)
- File transfer chunks
- Voice signaling
- ACK / retransmission
"""

from __future__ import annotations

import enum
import struct
import time
from dataclasses import field
from typing import Any, Optional

import msgspec


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

MAGIC = b"PSPM"  # Presidium Protocol Magic
VERSION = 1
HEADER_SIZE = 4 + 1 + 1 + 8 + 4  # magic + version + type + timestamp + payload_len
MAX_PAYLOAD_SIZE = 65536  # 64 KiB per datagram


# ---------------------------------------------------------------------------
# Message types enum
# ---------------------------------------------------------------------------

class MessageType(enum.Uint8):
    """Wire-level message type identifiers."""
    # Handshake
    HELLO = 1
    HELLO_ACK = 2
    PUNCH = 3
    PUNCH_ACK = 4

    # Key exchange
    KEY_EXCHANGE = 10
    KEY_EXCHANGE_ACK = 11

    # Chat
    CHAT_MESSAGE = 20
    CHAT_ACK = 21
    CHAT_READ = 22

    # DHT
    DHT_PING = 30
    DHT_PING_ACK = 31
    DHT_FIND_NODE = 32
    DHT_FIND_NODE_RESP = 33
    DHT_FIND_VALUE = 34
    DHT_FIND_VALUE_RESP = 35
    DHT_STORE = 36
    DHT_STORE_ACK = 37

    # File transfer
    FILE_OFFER = 40
    FILE_ACCEPT = 41
    FILE_CHUNK = 42
    FILE_CHUNK_ACK = 43
    FILE_DONE = 44

    # Voice signaling
    VOICE_OFFER = 50
    VOICE_ANSWER = 51
    VOICE_ICE_CANDIDATE = 52
    VOICE_HANGUP = 53

    # Keepalive / misc
    PING = 60
    PONG = 61
    DISCONNECT = 62
    ERROR = 63


# ---------------------------------------------------------------------------
# Message structs (msgspec)
# ---------------------------------------------------------------------------

class ProtocolHeader(msgspec.Struct):
    """Wire header for every Presidium message."""
    magic: bytes = field(default_factory=lambda: MAGIC)
    version: int = VERSION
    msg_type: int = 0
    timestamp: float = 0.0
    payload_length: int = 0


class HelloPayload(msgspec.Struct):
    """Initial handshake payload."""
    user_id: bytes  # 16 bytes
    public_key: bytes  # 32 bytes Ed25519
    listen_port: int
    supported_versions: list[int] = field(default_factory=lambda: [VERSION])


class HelloAckPayload(msgspec.Struct):
    """Handshake acknowledgement."""
    user_id: bytes
    public_key: bytes
    accepted: bool = True
    error_msg: str = ""


class PunchPayload(msgspec.Struct):
    """NAT hole-punching probe."""
    user_id: bytes
    external_addr: str  # observed external IP:port


class PunchAckPayload(msgspec.Struct):
    """NAT hole-punching acknowledgement."""
    user_id: bytes
    external_addr: str
    your_external_addr: str  # what we see as the sender's external addr


class KeyExchangePayload(msgspec.Struct):
    """X25519 ephemeral key exchange for E2EE session."""
    sender_user_id: bytes
    ephemeral_public_key: bytes  # 32 bytes X25519
    session_id: bytes  # 16 bytes random
    signed_payload: bytes  # Ed25519 signature of the above fields


class KeyExchangeAckPayload(msgspec.Struct):
    """Key exchange acknowledgement."""
    responder_user_id: bytes
    ephemeral_public_key: bytes  # 32 bytes X25519
    session_id: bytes  # must match the initiator's session_id
    signed_payload: bytes


class ChatMessagePayload(msgspec.Struct):
    """Encrypted chat message."""
    session_id: bytes  # 16 bytes
    sender_user_id: bytes
    message_id: bytes  # 16 bytes random UUID-like
    encrypted_content: bytes  # AES-256-GCM ciphertext
    timestamp: float
    reply_to: bytes = b""  # message_id of replied-to message, or empty


class ChatAckPayload(msgspec.Struct):
    """Delivery acknowledgement for a chat message."""
    session_id: bytes
    message_id: bytes
    status: int  # 0=received, 1=delivered, 2=read


class ChatReadPayload(msgspec.Struct):
    """Mark messages as read."""
    session_id: bytes
    message_ids: list[bytes]


class DHTPingPayload(msgspec.Struct):
    """DHT ping."""
    sender_id: bytes  # 16 bytes user_id
    sender_addr: str  # host:port


class DHTPingAckPayload(msgspec.Struct):
    """DHT ping acknowledgement."""
    sender_id: bytes
    sender_addr: str


class DHTFindNodePayload(msgspec.Struct):
    """DHT FIND_NODE: find k closest nodes to target."""
    sender_id: bytes
    target_id: bytes  # 16 bytes
    sender_addr: str


class DHTNodeInfo(msgspec.Struct):
    """Information about a DHT node."""
    user_id: bytes
    addr: str  # host:port
    public_key: bytes  # 32 bytes


class DHTFindNodeRespPayload(msgspec.Struct):
    """DHT FIND_NODE response."""
    sender_id: bytes
    target_id: bytes
    nodes: list[DHTNodeInfo]


class DHTStorePayload(msgspec.Struct):
    """DHT STORE: store a key-value pair."""
    sender_id: bytes
    key: bytes  # 16 bytes
    value: bytes
    ttl: int = 3600  # seconds
    signature: bytes = b""  # Ed25519 signature of (key + value)


class DHTStoreAckPayload(msgspec.Struct):
    """DHT STORE acknowledgement."""
    key: bytes
    stored: bool
    error_msg: str = ""


class DHTFindValuePayload(msgspec.Struct):
    """DHT FIND_VALUE: retrieve a value by key."""
    sender_id: bytes
    key: bytes


class DHTFindValueRespPayload(msgspec.Struct):
    """DHT FIND_VALUE response."""
    sender_id: bytes
    key: bytes
    value: bytes = b""
    found: bool = False
    closest_nodes: list[DHTNodeInfo] = field(default_factory=list)


class FileOfferPayload(msgspec.Struct):
    """Offer a file for transfer."""
    session_id: bytes
    file_name: str
    file_size: int
    chunk_size: int = 16384
    sha256: bytes  # 32 bytes
    total_chunks: int


class FileAcceptPayload(msgspec.Struct):
    """Accept a file transfer."""
    session_id: bytes
    start_chunk: int = 0  # for resume


class FileChunkPayload(msgspec.Struct):
    """A single chunk of a file transfer."""
    session_id: bytes
    chunk_index: int
    data: bytes
    chunk_sha256: bytes  # 32 bytes checksum of this chunk


class FileChunkAckPayload(msgspec.Struct):
    """Acknowledgement of a received file chunk."""
    session_id: bytes
    chunk_index: int


class FileDonePayload(msgspec.Struct):
    """File transfer complete."""
    session_id: bytes
    success: bool
    error_msg: str = ""


class VoiceOfferPayload(msgspec.Struct):
    """WebRTC voice call offer (SDP)."""
    session_id: bytes
    sdp: str
    media_types: list[str] = field(default_factory=lambda: ["audio"])


class VoiceAnswerPayload(msgspec.Struct):
    """WebRTC voice call answer (SDP)."""
    session_id: bytes
    sdp: str


class VoiceIceCandidatePayload(msgspec.Struct):
    """WebRTC ICE candidate."""
    session_id: bytes
    candidate: str
    sdp_mid: str = ""
    sdp_mline_index: int = 0


class VoiceHangupPayload(msgspec.Struct):
    """Hang up a voice call."""
    session_id: bytes
    reason: str = ""


class PingPayload(msgspec.Struct):
    """Keepalive ping."""
    sender_id: bytes


class PongPayload(msgspec.Struct):
    """Keepalive pong."""
    sender_id: bytes
    ping_timestamp: float


class DisconnectPayload(msgspec.Struct):
    """Graceful disconnect notification."""
    user_id: bytes
    reason: str = ""


class ErrorPayload(msgspec.Struct):
    """Error response."""
    code: int
    message: str


# ---------------------------------------------------------------------------
# Payload type registry: msg_type → Struct class
# ---------------------------------------------------------------------------

PAYLOAD_REGISTRY: dict[int, type[msgspec.Struct]] = {
    MessageType.HELLO: HelloPayload,
    MessageType.HELLO_ACK: HelloAckPayload,
    MessageType.PUNCH: PunchPayload,
    MessageType.PUNCH_ACK: PunchAckPayload,
    MessageType.KEY_EXCHANGE: KeyExchangePayload,
    MessageType.KEY_EXCHANGE_ACK: KeyExchangeAckPayload,
    MessageType.CHAT_MESSAGE: ChatMessagePayload,
    MessageType.CHAT_ACK: ChatAckPayload,
    MessageType.CHAT_READ: ChatReadPayload,
    MessageType.DHT_PING: DHTPingPayload,
    MessageType.DHT_PING_ACK: DHTPingAckPayload,
    MessageType.DHT_FIND_NODE: DHTFindNodePayload,
    MessageType.DHT_FIND_NODE_RESP: DHTFindNodeRespPayload,
    MessageType.DHT_FIND_VALUE: DHTFindValuePayload,
    MessageType.DHT_FIND_VALUE_RESP: DHTFindValueRespPayload,
    MessageType.DHT_STORE: DHTStorePayload,
    MessageType.DHT_STORE_ACK: DHTStoreAckPayload,
    MessageType.FILE_OFFER: FileOfferPayload,
    MessageType.FILE_ACCEPT: FileAcceptPayload,
    MessageType.FILE_CHUNK: FileChunkPayload,
    MessageType.FILE_CHUNK_ACK: FileChunkAckPayload,
    MessageType.FILE_DONE: FileDonePayload,
    MessageType.VOICE_OFFER: VoiceOfferPayload,
    MessageType.VOICE_ANSWER: VoiceAnswerPayload,
    MessageType.VOICE_ICE_CANDIDATE: VoiceIceCandidatePayload,
    MessageType.VOICE_HANGUP: VoiceHangupPayload,
    MessageType.PING: PingPayload,
    MessageType.PONG: PongPayload,
    MessageType.DISCONNECT: DisconnectPayload,
    MessageType.ERROR: ErrorPayload,
}


# ---------------------------------------------------------------------------
# Encode / decode
# ---------------------------------------------------------------------------

_encoder = msgspec.msgpack.Encoder()
_decoder = msgspec.msgpack.Decoder()


def encode_message(msg_type: int, payload: msgspec.Struct) -> bytes:
    """Encode a protocol message into bytes: header + payload.

    Args:
        msg_type: Message type from MessageType enum.
        payload: Corresponding msgspec Struct payload.

    Returns:
        Bytes ready to send over the wire.
    """
    payload_bytes = _encoder.encode(payload)
    if len(payload_bytes) > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload too large: {len(payload_bytes)} bytes, max {MAX_PAYLOAD_SIZE}. "
            "Use chunked transfer for large data."
        )

    header = struct.pack(
        "!4sBBdI",
        MAGIC,
        VERSION,
        msg_type,
        time.time(),
        len(payload_bytes),
    )
    return header + payload_bytes


def decode_message(data: bytes | memoryview) -> tuple[int, float, msgspec.Struct]:
    """Decode a protocol message from bytes.

    Args:
        data: Raw bytes received from the network.

    Returns:
        Tuple of (msg_type, timestamp, payload_struct).

    Raises:
        ProtocolError: If the message is malformed.
    """
    if len(data) < HEADER_SIZE:
        raise ProtocolError(f"Message too short: {len(data)} bytes, need at least {HEADER_SIZE}.")

    magic, version, msg_type, timestamp, payload_len = struct.unpack("!4sBBdI", data[:HEADER_SIZE])

    if magic != MAGIC:
        raise ProtocolError(f"Invalid magic: {magic!r}, expected {MAGIC!r}.")
    if version != VERSION:
        raise ProtocolError(f"Unsupported protocol version: {version}, expected {VERSION}.")
    if payload_len > MAX_PAYLOAD_SIZE:
        raise ProtocolError(f"Payload too large: {payload_len}, max {MAX_PAYLOAD_SIZE}.")

    payload_data = data[HEADER_SIZE:]
    if len(payload_data) < payload_len:
        raise ProtocolError(
            f"Incomplete payload: got {len(payload_data)} bytes, expected {payload_len}."
        )

    payload_cls = PAYLOAD_REGISTRY.get(msg_type)
    if payload_cls is None:
        raise ProtocolError(f"Unknown message type: {msg_type}.")

    try:
        payload = msgspec.msgpack.decode(payload_data[:payload_len], type=payload_cls)
    except Exception as exc:
        raise ProtocolError(f"Failed to decode payload of type {msg_type}: {exc}") from exc

    return msg_type, timestamp, payload


class ProtocolError(Exception):
    """Error in protocol encoding/decoding."""
