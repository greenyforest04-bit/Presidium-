"""Tests for the binary protocol encoding/decoding.

Tests round-trip serialization of all message payload types.
"""

from __future__ import annotations

import os
import pytest

from presidium.core.network.protocol import (
    MessageType,
    encode_message,
    decode_message,
    ProtocolError,
    HelloPayload,
    HelloAckPayload,
    PunchPayload,
    PunchAckPayload,
    KeyExchangePayload,
    KeyExchangeAckPayload,
    ChatMessagePayload,
    ChatAckPayload,
    ChatReadPayload,
    DHTPingPayload,
    DHTFindNodePayload,
    DHTFindNodeRespPayload,
    DHTNodeInfo,
    DHTStorePayload,
    DHTStoreAckPayload,
    DHTFindValuePayload,
    DHTFindValueRespPayload,
    FileOfferPayload,
    FileAcceptPayload,
    FileChunkPayload,
    FileChunkAckPayload,
    FileDonePayload,
    VoiceOfferPayload,
    VoiceAnswerPayload,
    VoiceIceCandidatePayload,
    VoiceHangupPayload,
    PingPayload,
    PongPayload,
    DisconnectPayload,
    ErrorPayload,
    TypingStartPayload,
    TypingStopPayload,
    ReactionPayload,
)


class TestProtocolRoundTrip:
    """Test that every message type can be encoded and decoded without loss."""

    def _round_trip(self, msg_type: int, payload) -> None:
        """Encode a message, decode it, and verify all fields match."""
        encoded = encode_message(msg_type, payload)
        decoded_type, decoded_ts, decoded_payload = decode_message(encoded)
        assert decoded_type == msg_type
        assert isinstance(decoded_payload, type(payload))

    def test_hello(self) -> None:
        p = HelloPayload(
            user_id=os.urandom(16),
            public_key=os.urandom(32),
            listen_port=9478,
        )
        self._round_trip(MessageType.HELLO, p)

    def test_hello_ack(self) -> None:
        p = HelloAckPayload(
            user_id=os.urandom(16),
            public_key=os.urandom(32),
            accepted=True,
            error_msg="test error",
        )
        self._round_trip(MessageType.HELLO_ACK, p)

    def test_punch(self) -> None:
        p = PunchPayload(
            user_id=os.urandom(16),
            external_addr="1.2.3.4:9478",
        )
        self._round_trip(MessageType.PUNCH, p)

    def test_punch_ack(self) -> None:
        p = PunchAckPayload(
            user_id=os.urandom(16),
            external_addr="1.2.3.4:9478",
            your_external_addr="5.6.7.8:9478",
        )
        self._round_trip(MessageType.PUNCH_ACK, p)

    def test_key_exchange(self) -> None:
        p = KeyExchangePayload(
            sender_user_id=os.urandom(16),
            ephemeral_public_key=os.urandom(32),
            session_id=os.urandom(16),
            signed_payload=os.urandom(64),
        )
        self._round_trip(MessageType.KEY_EXCHANGE, p)

    def test_key_exchange_ack(self) -> None:
        p = KeyExchangeAckPayload(
            responder_user_id=os.urandom(16),
            ephemeral_public_key=os.urandom(32),
            session_id=os.urandom(16),
            signed_payload=os.urandom(64),
        )
        self._round_trip(MessageType.KEY_EXCHANGE_ACK, p)

    def test_chat_message(self) -> None:
        p = ChatMessagePayload(
            session_id=os.urandom(16),
            sender_user_id=os.urandom(16),
            message_id=os.urandom(16),
            encrypted_content=os.urandom(64),
            timestamp=1234567890.0,
            reply_to=os.urandom(16),
        )
        self._round_trip(MessageType.CHAT_MESSAGE, p)

    def test_chat_ack(self) -> None:
        p = ChatAckPayload(
            session_id=os.urandom(16),
            message_id=os.urandom(16),
            status=2,
        )
        self._round_trip(MessageType.CHAT_ACK, p)

    def test_chat_read(self) -> None:
        p = ChatReadPayload(
            session_id=os.urandom(16),
            message_ids=[os.urandom(16), os.urandom(16)],
        )
        self._round_trip(MessageType.CHAT_READ, p)

    def test_dht_ping(self) -> None:
        p = DHTPingPayload(
            sender_id=os.urandom(16),
            sender_addr="1.2.3.4:9478",
        )
        self._round_trip(MessageType.DHT_PING, p)

    def test_dht_find_node(self) -> None:
        p = DHTFindNodePayload(
            sender_id=os.urandom(16),
            target_id=os.urandom(16),
            sender_addr="1.2.3.4:9478",
        )
        self._round_trip(MessageType.DHT_FIND_NODE, p)

    def test_dht_find_node_resp(self) -> None:
        nodes = [
            DHTNodeInfo(user_id=os.urandom(16), addr="1.2.3.4:9478", public_key=os.urandom(32))
            for _ in range(3)
        ]
        p = DHTFindNodeRespPayload(
            sender_id=os.urandom(16),
            target_id=os.urandom(16),
            nodes=nodes,
        )
        self._round_trip(MessageType.DHT_FIND_NODE_RESP, p)

    def test_dht_store(self) -> None:
        p = DHTStorePayload(
            sender_id=os.urandom(16),
            key=os.urandom(16),
            value=os.urandom(64),
            ttl=3600,
            signature=os.urandom(64),
        )
        self._round_trip(MessageType.DHT_STORE, p)

    def test_dht_find_value(self) -> None:
        p = DHTFindValuePayload(
            sender_id=os.urandom(16),
            key=os.urandom(16),
        )
        self._round_trip(MessageType.DHT_FIND_VALUE, p)

    def test_file_offer(self) -> None:
        p = FileOfferPayload(
            session_id=os.urandom(16),
            file_name="test.txt",
            file_size=1024,
            chunk_size=16384,
            sha256=os.urandom(32),
            total_chunks=1,
        )
        self._round_trip(MessageType.FILE_OFFER, p)

    def test_file_chunk(self) -> None:
        p = FileChunkPayload(
            session_id=os.urandom(16),
            chunk_index=0,
            data=os.urandom(16384),
            chunk_sha256=os.urandom(32),
        )
        self._round_trip(MessageType.FILE_CHUNK, p)

    def test_voice_offer(self) -> None:
        p = VoiceOfferPayload(
            session_id=os.urandom(16),
            sdp="v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\n",
            media_types=["audio"],
        )
        self._round_trip(MessageType.VOICE_OFFER, p)

    def test_voice_ice_candidate(self) -> None:
        p = VoiceIceCandidatePayload(
            session_id=os.urandom(16),
            candidate="candidate:1 1 UDP 2130706431 192.168.1.1 9478 typ host",
            sdp_mid="0",
            sdp_mline_index=0,
        )
        self._round_trip(MessageType.VOICE_ICE_CANDIDATE, p)

    def test_typing_start(self) -> None:
        p = TypingStartPayload(
            sender_user_id=os.urandom(16),
            session_id=os.urandom(16),
        )
        self._round_trip(MessageType.TYPING_START, p)

    def test_typing_stop(self) -> None:
        p = TypingStopPayload(
            sender_user_id=os.urandom(16),
            session_id=os.urandom(16),
        )
        self._round_trip(MessageType.TYPING_STOP, p)

    def test_reaction(self) -> None:
        p = ReactionPayload(
            sender_user_id=os.urandom(16),
            message_id=os.urandom(16),
            emoji="\U0001f44d",
            remove=False,
        )
        self._round_trip(MessageType.REACTION, p)

    def test_ping(self) -> None:
        p = PingPayload(sender_id=os.urandom(16))
        self._round_trip(MessageType.PING, p)

    def test_disconnect(self) -> None:
        p = DisconnectPayload(user_id=os.urandom(16), reason="bye")
        self._round_trip(MessageType.DISCONNECT, p)

    def test_error(self) -> None:
        p = ErrorPayload(code=404, message="Not found")
        self._round_trip(MessageType.ERROR, p)


class TestProtocolErrors:
    def test_too_short_data(self) -> None:
        with pytest.raises(ProtocolError, match="too short"):
            decode_message(os.urandom(10))

    def test_invalid_magic(self) -> None:
        from presidium.core.network.protocol import HEADER_SIZE, MAGIC, VERSION
        # Build a message with wrong magic
        import struct
        import time
        header = struct.pack("!4sBBdI", b"XXXX", VERSION, MessageType.PING, time.time(), 0)
        with pytest.raises(ProtocolError, match="Invalid magic"):
            decode_message(header)

    def test_unknown_message_type(self) -> None:
        from presidium.core.network.protocol import HEADER_SIZE, MAGIC, VERSION
        import struct
        import time
        header = struct.pack("!4sBBdI", MAGIC, VERSION, 99, time.time(), 0)
        with pytest.raises(ProtocolError, match="Unknown message type"):
            decode_message(header)
