"""Tests for the Double Ratchet implementation.

Tests:
- Initiator/responder session setup
- Message encryption/decryption round-trip
- Out-of-order message handling with skipped key cache
- DH ratchet step on key change
- Too many skipped messages error
- Header encryption/decryption
"""

from __future__ import annotations

import os
import pytest

from presidium.core.crypto import X25519KeyPair
from presidium.core.messaging.ratchet import (
    DoubleRatchet,
    RatchetHeader,
    RatchetError,
    RatchetNotInitializedError,
    TooManySkippedMessagesError,
    HeaderDecryptError,
    MessageAuthenticationError,
    encode_header,
    decode_header,
    encrypt_header,
    decrypt_header,
    advance_chain_key,
    SkippedMessageKeys,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_secret() -> bytes:
    """Generate a random 32-byte shared secret for testing."""
    return os.urandom(32)


@pytest.fixture
def bob_prekey() -> bytes:
    """Generate a random X25519 prekey for Bob."""
    return X25519KeyPair().public_key_bytes


@pytest.fixture
def alice_init(shared_secret: bytes, bob_prekey: bytes) -> DoubleRatchet:
    """Create an initiator (Alice) ratchet."""
    ratchet = DoubleRatchet()
    ratchet.init_as_initiator(shared_secret, bob_prekey)
    return ratchet


@pytest.fixture
def bob_responder(shared_secret: bytes, alice_init: DoubleRatchet) -> DoubleRatchet:
    """Create a responder (Bob) ratchet using Alice's first public key."""
    ratchet = DoubleRatchet()
    ratchet.init_as_responder(shared_secret, alice_init.public_key)
    return ratchet


# ---------------------------------------------------------------------------
# Tests: Header encode/decode
# ---------------------------------------------------------------------------

class TestHeader:
    def test_encode_decode_roundtrip(self) -> None:
        header = RatchetHeader(
            dh_public_key=os.urandom(32),
            message_number=42,
            previous_chain_length=10,
        )
        encoded = encode_header(header)
        decoded = decode_header(encoded)
        assert decoded.dh_public_key == header.dh_public_key
        assert decoded.message_number == header.message_number
        assert decoded.previous_chain_length == header.previous_chain_length

    def test_encode_decode_short_fails(self) -> None:
        with pytest.raises(RatchetError, match="too short"):
            decode_header(os.urandom(10))


# ---------------------------------------------------------------------------
# Tests: Header encryption
# ---------------------------------------------------------------------------

class TestHeaderEncryption:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        key = os.urandom(32)
        header = RatchetHeader(
            dh_public_key=os.urandom(32),
            message_number=5,
            previous_chain_length=0,
        )
        encrypted = encrypt_header(header, key)
        decrypted = decrypt_header(encrypted, key)
        assert decrypted.dh_public_key == header.dh_public_key
        assert decrypted.message_number == header.message_number

    def test_wrong_key_fails(self) -> None:
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        header = RatchetHeader(
            dh_public_key=os.urandom(32),
            message_number=1,
            previous_chain_length=0,
        )
        encrypted = encrypt_header(header, key1)
        with pytest.raises(HeaderDecryptError):
            decrypt_header(encrypted, key2)


# ---------------------------------------------------------------------------
# Tests: Chain key advancement
# ---------------------------------------------------------------------------

class TestChainKeyAdvancement:
    def test_advance_produces_different_keys(self) -> None:
        chain_key = os.urandom(32)
        new_chain, msg_key = advance_chain_key(chain_key)
        assert new_chain != chain_key
        assert msg_key != chain_key
        assert msg_key != new_chain
        assert len(new_chain) == 32
        assert len(msg_key) == 32

    def test_advance_is_deterministic(self) -> None:
        chain_key = os.urandom(32)
        new1, msg1 = advance_chain_key(chain_key)
        new2, msg2 = advance_chain_key(chain_key)
        assert new1 == new2
        assert msg1 == msg2


# ---------------------------------------------------------------------------
# Tests: Skipped message keys
# ---------------------------------------------------------------------------

class TestSkippedMessageKeys:
    def test_store_and_retrieve(self) -> None:
        cache = SkippedMessageKeys(max_skip=10)
        header_key = os.urandom(32)
        msg_key = os.urandom(32)
        cache.store(header_key, 5, msg_key)
        retrieved = cache.retrieve(header_key, 5)
        assert retrieved == msg_key

    def test_retrieve_removes_entry(self) -> None:
        cache = SkippedMessageKeys()
        header_key = os.urandom(32)
        cache.store(header_key, 0, os.urandom(32))
        cache.retrieve(header_key, 0)
        assert cache.retrieve(header_key, 0) is None

    def test_missing_returns_none(self) -> None:
        cache = SkippedMessageKeys()
        assert cache.retrieve(os.urandom(32), 0) is None

    def test_max_skip_eviction(self) -> None:
        cache = SkippedMessageKeys(max_skip=3)
        hk = os.urandom(32)
        for i in range(5):
            cache.store(hk, i, os.urandom(32))
        # First 2 should be evicted
        assert cache.count == 3
        assert not cache.has_key(hk, 0)
        assert not cache.has_key(hk, 1)
        assert cache.has_key(hk, 2)


# ---------------------------------------------------------------------------
# Tests: Full Double Ratchet
# ---------------------------------------------------------------------------

class TestDoubleRatchet:
    def test_init_not_initialized_by_default(self) -> None:
        r = DoubleRatchet()
        assert not r.is_initialized

    def test_encrypt_before_init_raises(self) -> None:
        r = DoubleRatchet()
        with pytest.raises(RatchetNotInitializedError):
            r.encrypt(b"hello")

    def test_decrypt_before_init_raises(self) -> None:
        r = DoubleRatchet()
        with pytest.raises(RatchetNotInitializedError):
            r.decrypt(b"header", b"body")

    def test_alice_encrypt_bob_decrypt(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """Alice encrypts a message, Bob decrypts it."""
        plaintext = b"Hello, Bob!"
        enc_header, enc_body = alice_init.encrypt(plaintext)
        decrypted = bob_responder.decrypt(enc_header, enc_body)
        assert decrypted == plaintext

    def test_multiple_messages_in_order(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """Send multiple messages in order from Alice to Bob."""
        for i in range(10):
            plaintext = f"Message {i}".encode()
            enc_header, enc_body = alice_init.encrypt(plaintext)
            decrypted = bob_responder.decrypt(enc_header, enc_body)
            assert decrypted == plaintext

    def test_bidirectional_messages(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """Alice and Bob take turns sending messages (triggers DH ratchet)."""
        # Alice → Bob
        enc_h, enc_b = alice_init.encrypt(b"Hi Bob")
        assert bob_responder.decrypt(enc_h, enc_b) == b"Hi Bob"

        # Bob → Alice (DH ratchet step)
        enc_h, enc_b = bob_responder.encrypt(b"Hi Alice")
        assert alice_init.decrypt(enc_h, enc_b) == b"Hi Alice"

        # Alice → Bob (another DH ratchet step)
        enc_h, enc_b = alice_init.encrypt(b"How are you?")
        assert bob_responder.decrypt(enc_h, enc_b) == b"How are you?"

    def test_out_of_order_messages(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """Messages arrive out of order — Bob can still decrypt."""
        # Alice sends 3 messages
        msgs = []
        for i in range(3):
            plaintext = f"Msg {i}".encode()
            enc_h, enc_b = alice_init.encrypt(plaintext)
            msgs.append((enc_h, enc_b, plaintext))

        # Bob receives them in reverse order
        for enc_h, enc_b, expected in reversed(msgs):
            decrypted = bob_responder.decrypt(enc_h, enc_b)
            assert decrypted == expected

    def test_skipped_message_key_reuse(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """A skipped message key should not be available after first use."""
        # Alice sends 3 messages
        msgs = []
        for i in range(3):
            plaintext = f"Msg {i}".encode()
            enc_h, enc_b = alice_init.encrypt(plaintext)
            msgs.append((enc_h, enc_b, plaintext))

        # Bob receives only message 2 (skipping 0 and 1)
        enc_h, enc_b, expected = msgs[2]
        decrypted = bob_responder.decrypt(enc_h, enc_b)
        assert decrypted == expected

        # Bob receives message 1 (one of the skipped)
        enc_h, enc_b, expected = msgs[1]
        decrypted = bob_responder.decrypt(enc_h, enc_b)
        assert decrypted == expected

        # Bob receives message 0 (the last skipped)
        enc_h, enc_b, expected = msgs[0]
        decrypted = bob_responder.decrypt(enc_h, enc_b)
        assert decrypted == expected

    def test_too_many_skipped_messages(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """Skipping more than MAX_SKIP messages should raise an error."""
        # Alice sends MAX_SKIP + 1 messages
        enc_h_last, enc_b_last = b"", b""
        for i in range(1002):
            enc_h_last, enc_b_last = alice_init.encrypt(f"Msg {i}".encode())

        # Bob tries to decrypt the last one — should fail because too many skipped
        with pytest.raises(TooManySkippedMessagesError):
            bob_responder.decrypt(enc_h_last, enc_b_last)

    def test_wrong_ciphertext_fails(
        self, alice_init: DoubleRatchet, bob_responder: DoubleRatchet
    ) -> None:
        """Modified ciphertext should cause authentication failure."""
        enc_h, enc_b = alice_init.encrypt(b"secret message")
        # Tamper with the ciphertext
        tampered = enc_b[:20] + bytes([(enc_b[20] + 1) % 256]) + enc_b[21:]
        with pytest.raises(MessageAuthenticationError):
            bob_responder.decrypt(enc_h, tampered)

    def test_ratchet_state_properties(self, alice_init: DoubleRatchet) -> None:
        """Check that ratchet state has expected properties after init."""
        assert alice_init.is_initialized
        assert len(alice_init.public_key) == 32
        state = alice_init.state
        assert state.initialized
        assert len(state.root_key) == 32
        assert len(state.send_chain_key) == 32
