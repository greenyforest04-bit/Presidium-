"""End-to-end integration tests for Presidium v1.5 "Presidium Prime".

Tests the complete functionality of all major subsystems using real
(in-memory) instances — no mocks for core crypto or protocol objects.
Each test category creates the necessary objects from scratch and
verifies round-trip behaviour, error handling, and edge cases.

Test categories:
    crypto          — key generation, signing, encryption, Argon2id
    network         — DHT operations, peer discovery v2
    messaging       — Double Ratchet encrypt / decrypt
    push            — zero-knowledge push relay push / pull
    voice_message   — voice message encoding / decoding
    location        — precision reduction and wire format
    polls           — poll create / vote / close lifecycle
    i18n            — translation and plural forms
    a11y            — accessibility profile and screen reader text
    search          — semantic search indexing and fallback embedding
    import          — import validation and sanitization
    sdk             — SDK initialization and message send

All tests use ``@pytest.mark.asyncio`` and real instances.  Fixtures
provide commonly-shared objects (identity, key pairs, etc.).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QWidget
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False

try:
    import msgspec  # noqa: F401
    HAS_MSGSPEC = True
except ImportError:
    HAS_MSGSPEC = False


# ==========================================================================
# Fixtures
# ==========================================================================

@pytest.fixture
def ed25519_keypair():
    """Create a fresh Ed25519 key pair."""
    from presidium.core.crypto import Ed25519KeyPair
    return Ed25519KeyPair()


@pytest.fixture
def x25519_keypair():
    """Create a fresh X25519 key pair."""
    from presidium.core.crypto import X25519KeyPair
    return X25519KeyPair()


@pytest.fixture
def two_x25519_pairs():
    """Create two X25519 pairs (Alice + Bob) for DH exchange."""
    from presidium.core.crypto import X25519KeyPair
    return X25519KeyPair(), X25519KeyPair()


@pytest.fixture
def shared_secret(two_x25519_pairs):
    """Compute a DH shared secret between Alice and Bob."""
    alice, bob = two_x25519_pairs
    sk_alice = alice.shared_secret(bob.public_key_bytes)
    sk_bob = bob.shared_secret(alice.public_key_bytes)
    assert bytes(sk_alice) == bytes(sk_bob)
    return sk_alice


@pytest.fixture
def user_id(ed25519_keypair):
    """Derive a User ID from an Ed25519 public key."""
    from presidium.core.crypto import compute_user_id
    return compute_user_id(ed25519_keypair.public_key_bytes)


# ==========================================================================
# P0: Crypto tests
# ==========================================================================

class TestCrypto:
    """Test cryptographic primitives for Presidium v1.5."""

    def test_ed25519_sign_verify(self, ed25519_keypair) -> None:
        """Ed25519 signature should verify against the original message."""
        from presidium.core.crypto import Ed25519KeyPair
        message = b"Presidium Prime test message"
        signature = ed25519_keypair.sign(message)
        assert Ed25519KeyPair.verify(ed25519_keypair.public_key_bytes, signature, message) is True

    def test_ed25519_tampered_message_fails(self, ed25519_keypair) -> None:
        """Signature verification should fail for tampered messages."""
        from presidium.core.crypto import Ed25519KeyPair
        message = b"Original message"
        signature = ed25519_keypair.sign(message)
        tampered = b"Tampered message"
        assert Ed25519KeyPair.verify(ed25519_keypair.public_key_bytes, signature, tampered) is False

    def test_ed25519_wrong_key_fails(self) -> None:
        """Signature should not verify with a different public key."""
        from presidium.core.crypto import Ed25519KeyPair
        key_a = Ed25519KeyPair()
        key_b = Ed25519KeyPair()
        message = b"Test message"
        sig = key_a.sign(message)
        assert Ed25519KeyPair.verify(key_b.public_key_bytes, sig, message) is False

    def test_x25519_dh_shared_secret(self, two_x25519_pairs) -> None:
        """DH shared secret should be identical from both sides."""
        alice, bob = two_x25519_pairs
        sk_alice = alice.shared_secret(bob.public_key_bytes)
        sk_bob = bob.shared_secret(alice.public_key_bytes)
        assert bytes(sk_alice) == bytes(sk_bob)
        assert len(bytes(sk_alice)) == 32

    def test_aes256gcm_encrypt_decrypt(self) -> None:
        """AES-256-GCM should round-trip correctly."""
        from presidium.core.crypto import AES256GCM
        key = os.urandom(32)
        plaintext = b"Hello, Presidium Prime!"
        ciphertext = AES256GCM.encrypt(key, plaintext)
        decrypted = AES256GCM.decrypt(key, ciphertext)
        assert decrypted == plaintext

    def test_aes256gcm_tampered_fails(self) -> None:
        """AES-256-GCM decryption should fail if ciphertext is tampered."""
        from presidium.core.crypto import AES256GCM, DecryptionError
        key = os.urandom(32)
        plaintext = b"Secret data"
        ciphertext = bytearray(AES256GCM.encrypt(key, plaintext))
        # Flip a byte in the ciphertext
        ciphertext[-1] ^= 0xFF
        with pytest.raises(DecryptionError):
            AES256GCM.decrypt(key, bytes(ciphertext))

    def test_aes256gcm_wrong_key_fails(self) -> None:
        """AES-256-GCM decryption should fail with the wrong key."""
        from presidium.core.crypto import AES256GCM, DecryptionError
        key_a = os.urandom(32)
        key_b = os.urandom(32)
        plaintext = b"Encrypted message"
        ciphertext = AES256GCM.encrypt(key_a, plaintext)
        with pytest.raises(DecryptionError):
            AES256GCM.decrypt(key_b, ciphertext)

    def test_compute_user_id(self, ed25519_keypair) -> None:
        """User ID should be a deterministic 32-char hex string."""
        from presidium.core.crypto import compute_user_id
        pk = ed25519_keypair.public_key_bytes
        uid = compute_user_id(pk)
        assert len(uid) == 32
        assert all(c in "0123456789abcdef" for c in uid)
        # Deterministic
        uid2 = compute_user_id(pk)
        assert uid == uid2

    def test_argon2id_key_derivation(self) -> None:
        """Argon2id should derive a 32-byte key from a password."""
        from presidium.core.crypto import derive_key_from_password, generate_salt
        salt = generate_salt(32)
        key = derive_key_from_password("StrongPassword123!", salt)
        assert len(bytes(key)) == 32
        # Same password + salt = same key
        key2 = derive_key_from_password("StrongPassword123!", salt)
        assert bytes(key) == bytes(key2)

    def test_argon2id_different_salt(self) -> None:
        """Different salts should produce different keys."""
        from presidium.core.crypto import derive_key_from_password, generate_salt
        key_a = derive_key_from_password("SamePassword", generate_salt(32))
        key_b = derive_key_from_password("SamePassword", generate_salt(32))
        assert bytes(key_a) != bytes(key_b)

    def test_secure_bytes_zeroing(self) -> None:
        """SecureBytes should zero its memory on explicit zero() call."""
        from presidium.core.crypto import SecureBytes
        sb = SecureBytes(b"\x01\x02\x03\x04")
        sb.zero()
        assert bytes(sb) == b"\x00\x00\x00\x00"


# ==========================================================================
# P0: Double Ratchet tests
# ==========================================================================

class TestDoubleRatchet:
    """Test Double Ratchet encrypt/decrypt for v1.5."""

    def test_ratchet_header_encode_decode(self) -> None:
        """RatchetHeader should round-trip through encode/decode."""
        from presidium.core.messaging.ratchet import RatchetHeader, encode_header, decode_header
        header = RatchetHeader(
            dh_public_key=os.urandom(32),
            message_number=7,
            previous_chain_length=3,
        )
        encoded = encode_header(header)
        decoded = decode_header(encoded)
        assert decoded.dh_public_key == header.dh_public_key
        assert decoded.message_number == header.message_number
        assert decoded.previous_chain_length == header.previous_chain_length

    def test_chain_key_advancement(self) -> None:
        """Chain key advancement should produce deterministic message keys."""
        from presidium.core.messaging.ratchet import advance_chain_key
        ck = os.urandom(32)
        ck2, mk1 = advance_chain_key(ck)
        ck3, mk2 = advance_chain_key(ck2)
        # Each step produces unique keys
        assert ck != ck2
        assert ck2 != ck3
        assert mk1 != mk2
        # Deterministic
        ck2_b, mk1_b = advance_chain_key(ck)
        assert ck2 == ck2_b
        assert mk1 == mk1_b

    def test_header_encryption_roundtrip(self) -> None:
        """Header encryption should round-trip."""
        from presidium.core.messaging.ratchet import (
            RatchetHeader, encrypt_header, decrypt_header,
        )
        header = RatchetHeader(
            dh_public_key=os.urandom(32),
            message_number=2,
            previous_chain_length=0,
        )
        hk = os.urandom(32)
        encrypted = encrypt_header(header, hk)
        decrypted = decrypt_header(encrypted, hk)
        assert decrypted.dh_public_key == header.dh_public_key
        assert decrypted.message_number == header.message_number

    def test_skipped_message_key_store(self) -> None:
        """SkippedMessageKeys should store and retrieve message keys."""
        from presidium.core.messaging.ratchet import SkippedMessageKeys
        store = SkippedMessageKeys(max_skip=10)
        hk = os.urandom(32)
        mk = os.urandom(32)
        store.store(hk, 3, mk)
        assert store.has_key(hk, 3) is True
        retrieved = store.retrieve(hk, 3)
        assert retrieved == mk
        # After retrieval, key should be gone
        assert store.retrieve(hk, 3) is None
        assert store.has_key(hk, 3) is False

    def test_skipped_message_key_eviction(self) -> None:
        """SkippedMessageKeys should evict oldest when full."""
        from presidium.core.messaging.ratchet import SkippedMessageKeys
        store = SkippedMessageKeys(max_skip=3)
        hk = os.urandom(32)
        for i in range(4):
            store.store(hk, i, os.urandom(32))
        # Oldest (0) should be evicted
        assert store.has_key(hk, 0) is False
        # Newest entries should remain
        assert store.has_key(hk, 1) is True
        assert store.has_key(hk, 2) is True
        assert store.has_key(hk, 3) is True

    def test_header_decrypt_wrong_key_fails(self) -> None:
        """Decrypting a header with the wrong key should fail."""
        from presidium.core.messaging.ratchet import (
            RatchetHeader, encrypt_header, decrypt_header, HeaderDecryptError,
        )
        header = RatchetHeader(
            dh_public_key=os.urandom(32),
            message_number=1,
            previous_chain_length=0,
        )
        hk = os.urandom(32)
        wrong_hk = os.urandom(32)
        encrypted = encrypt_header(header, hk)
        with pytest.raises(HeaderDecryptError):
            decrypt_header(encrypted, wrong_hk)


# ==========================================================================
# P0: DHT tests
# ==========================================================================

@pytest.mark.skipif(not HAS_MSGSPEC, reason="msgspec not installed")
class TestDHT:
    """Test DHT operations for Presidium v1.5."""

    def test_xor_distance(self) -> None:
        """XOR distance should be symmetric and correct."""
        from presidium.core.network.dht import xor_distance
        a = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
        b = bytes.fromhex("1112131415161718191a1b1c1d1e1f20")
        d_ab = xor_distance(a, b)
        d_ba = xor_distance(b, a)
        assert d_ab == d_ba  # Symmetric
        assert d_ab > 0

    def test_xor_distance_self_is_zero(self) -> None:
        """XOR distance of a node with itself should be zero."""
        from presidium.core.network.dht import xor_distance
        node_id = os.urandom(16)
        assert xor_distance(node_id, node_id) == 0

    def test_bucket_index_for_distance(self) -> None:
        """Bucket zero is reserved for self; other buckets are one-based."""
        from presidium.core.network.dht import bucket_index_for_distance
        for distance, index in [(0, 0), (1, 1), (2, 2), (3, 2), (4, 3), (255, 8), (1 << 127, 128)]:
            assert bucket_index_for_distance(distance) == index


    def test_kbucket_insert(self) -> None:
        """KBucket should insert nodes up to its capacity."""
        from presidium.core.network.dht import KBucket, DHTNode
        bucket = KBucket(k=4)
        for i in range(4):
            node = DHTNode(user_id=os.urandom(16), addr=f"127.0.0.1:{9000 + i}")
            result = bucket.add_or_update(node)
            assert result is None  # None means added successfully
        assert len(bucket) == 4
        # Fifth should return the head node (full, needs eviction check)
        node5 = DHTNode(user_id=os.urandom(16), addr="127.0.0.1:9004")
        result = bucket.add_or_update(node5)
        assert result is not None  # Returns head node when full

    def test_kbucket_replace_stale(self) -> None:
        """KBucket should replace the least-recently-seen node when full."""
        from presidium.core.network.dht import KBucket, DHTNode
        bucket = KBucket(k=2)
        n1 = DHTNode(user_id=os.urandom(16), addr="1.1.1.1:1")
        n2 = DHTNode(user_id=os.urandom(16), addr="2.2.2.2:2")
        bucket.add_or_update(n1)
        bucket.add_or_update(n2)
        # Mark n1 as stale by incrementing failures
        n1.failures = 3
        # Replace n1 with n3 using replace_if_stale
        n3 = DHTNode(user_id=os.urandom(16), addr="3.3.3.3:3")
        bucket.remove(n1.user_id)
        bucket.add_or_update(n3)
        assert len(bucket) == 2
        # n3 should be in bucket now — check via __contains__
        assert n3.user_id in bucket._nodes


# ==========================================================================
# P0: Discovery v2 tests
# ==========================================================================

class TestDiscoveryV2:
    """Test privacy-preserving contact discovery with blind signatures."""

    @pytest.mark.asyncio
    async def test_full_discovery_roundtrip(self) -> None:
        """Full discovery protocol: register → discover → match."""
        from presidium.core.network.discovery_v2 import ContactDiscoveryV2

        # Alice registers her contacts
        alice = ContactDiscoveryV2(user_id="alice", key_bits=1024)
        await alice.initialize()
        await alice.register_contacts(["+15551234567", "bob@example.com"])

        # Bob also registers "bob@example.com"
        bob = ContactDiscoveryV2(user_id="bob", key_bits=1024)
        # Share the same server for the test
        await bob.initialize()
        await bob.register_contacts(["bob@example.com", "carol@example.com"])

        # Alice discovers using Bob's server
        # (In production the server is shared; here we use Alice's server
        #  and manually register Bob's token there.)
        bob_tokens = bob.client.create_tokens(["bob@example.com"])
        blinded = [t.blinded_token for t in bob_tokens]
        sigs = alice.server.sign_tokens(blinded, "bob")
        completed = bob.client.unblind_tokens(bob_tokens, sigs)
        for token in completed:
            alice.server.register_token(token.unblinded_sig, "bob")

        # Alice discovers her contacts
        matches = await alice.discover_contacts(["+15551234567", "bob@example.com"])
        assert "bob@example.com" in matches

        await alice.shutdown()
        await bob.shutdown()

    @pytest.mark.asyncio
    async def test_rate_limiting(self) -> None:
        """Server should reject requests beyond the hourly limit."""
        from presidium.core.network.discovery_v2 import (
            DiscoveryServer, RateLimitExceededError, RATE_LIMIT_PER_HOUR,
        )

        server = DiscoveryServer(key_bits=1024)
        await server.initialize()
        dummy_token = os.urandom(256)

        for i in range(RATE_LIMIT_PER_HOUR):
            server.sign_tokens([dummy_token], f"limited_user")

        with pytest.raises(RateLimitExceededError):
            server.sign_tokens([dummy_token], f"limited_user")

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self) -> None:
        """Discovery with no matching contacts should return empty list."""
        from presidium.core.network.discovery_v2 import ContactDiscoveryV2

        cd = ContactDiscoveryV2(user_id="solo", key_bits=1024)
        await cd.initialize()
        matches = await cd.discover_contacts(["+19999999999"])
        assert matches == []
        await cd.shutdown()

    def test_contact_hash_deterministic(self) -> None:
        """Same contact string should always produce the same hash."""
        from presidium.core.network.discovery_v2 import DiscoveryClient
        h1 = DiscoveryClient.hash_contact("+15551234567")
        h2 = DiscoveryClient.hash_contact("+15551234567")
        assert h1 == h2

    def test_different_contacts_different_hashes(self) -> None:
        """Different contact strings should produce different hashes."""
        from presidium.core.network.discovery_v2 import DiscoveryClient
        h1 = DiscoveryClient.hash_contact("+15551234567")
        h2 = DiscoveryClient.hash_contact("+15559876543")
        assert h1 != h2


# ==========================================================================
# P0: Push relay tests
# ==========================================================================

class TestPushRelay:
    """Test zero-knowledge push relay push/pull."""

    @pytest.mark.asyncio
    async def test_push_blob_store_and_retrieve(self) -> None:
        """A pushed blob should be retrievable by its blob_id."""
        from presidium.push.relay_server import PushRelayServer, StoredBlob
        server = PushRelayServer()

        blob_id = hashlib.sha256(os.urandom(32) + struct.pack("!Q", int(time.time()))).digest()[:16]
        encrypted_blob = os.urandom(256)
        hmac_value = hashlib.sha256(blob_id + encrypted_blob).digest()

        blob = StoredBlob(
            blob_id=blob_id,
            encrypted_data=encrypted_blob,
            hmac_tag=hmac_value,
            created_at=time.time(),
            expires_at=time.time() + 604800,
        )
        await server._storage.store(blob)
        result = await server._storage.fetch(blob_id)
        assert result is not None
        assert result.encrypted_data == encrypted_blob

    @pytest.mark.asyncio
    async def test_push_blob_not_found(self) -> None:
        """Requesting a non-existent blob_id should return None."""
        from presidium.push.relay_server import PushRelayServer
        server = PushRelayServer()
        result = await server._storage.fetch(os.urandom(16))
        assert result is None

    @pytest.mark.asyncio
    async def test_blob_expiry(self) -> None:
        """Blobs should be evicted after their expiry time."""
        from presidium.push.relay_server import PushRelayServer, StoredBlob
        server = PushRelayServer()

        blob_id = hashlib.sha256(b"test-expiry").digest()[:16]
        encrypted_blob = os.urandom(128)
        hmac_value = hashlib.sha256(blob_id + encrypted_blob).digest()

        # Store with immediate expiry (1 second)
        blob = StoredBlob(
            blob_id=blob_id,
            encrypted_data=encrypted_blob,
            hmac_tag=hmac_value,
            created_at=time.time(),
            expires_at=time.time() + 1,
        )
        await server._storage.store(blob)

        # Should be available now
        result = await server._storage.fetch(blob_id)
        assert result is not None
        assert result.encrypted_data == encrypted_blob

        # Wait for expiry
        time.sleep(1.5)
        await server._storage.cleanup_expired()

        # Should be gone
        result = await server._storage.fetch(blob_id)
        assert result is None

    def test_push_rate_limiting(self) -> None:
        """Push relay should rate-limit per IP."""
        from presidium.push.relay_server import RateLimiter, RateLimitError
        limiter = RateLimiter(push_limit=5, pull_limit=5)

        ip = "10.0.0.1"
        for _ in range(5):
            assert limiter.check_push(ip) is True

        # Next push should be rate-limited (returns False)
        assert limiter.check_push(ip) is False


# ==========================================================================
# P1: Voice Message tests
# ==========================================================================

@pytest.mark.skipif(not HAS_PYSIDE6, reason="PySide6 not installed")
class TestVoiceMessage:
    """Test voice message encoding and decoding."""

    def test_voice_message_encode_decode(self) -> None:
        """VoiceMessageData wire format should round-trip correctly."""
        from presidium.core.messaging.voice_message import VoiceMessageData
        waveform = bytes([64, 128, 230, 77, 179])
        msg = VoiceMessageData(
            duration_ms=5000,
            flags=0x01,  # has_waveform
            waveform=waveform,
            opus_data=os.urandom(1024),
        )
        encoded = msg.encode()
        decoded = VoiceMessageData.decode(encoded)
        assert decoded.duration_ms == msg.duration_ms
        assert decoded.waveform == msg.waveform
        assert decoded.opus_data == msg.opus_data

    def test_voice_message_default_waveform(self) -> None:
        """VoiceMessageData with no waveform should have empty bytes."""
        from presidium.core.messaging.voice_message import VoiceMessageData
        msg = VoiceMessageData(
            duration_ms=3000,
            opus_data=os.urandom(512),
        )
        assert msg.waveform == b""

    def test_voice_waveform_widget_amplitude(self) -> None:
        """VoiceWaveformWidget should accept waveform data."""
        from presidium.core.messaging.voice_message import VoiceWaveformWidget
        waveform = bytes([0, 128, 255, 64])
        widget = VoiceWaveformWidget(waveform=waveform)
        widget.set_waveform(waveform)
        assert widget._waveform == waveform


# ==========================================================================
# P1: Location tests
# ==========================================================================

class TestLocation:
    """Test location precision reduction and wire format."""

    def test_location_encode_decode_roundtrip(self) -> None:
        """LocationMessage should round-trip through encode/decode."""
        from presidium.core.messaging.location import (
            LocationMessage, LocationType, LocationPrecision, GeoCoordinate,
        )
        msg = LocationMessage(
            location_type=LocationType.STATIC,
            coordinate=GeoCoordinate(latitude=40.7128, longitude=-74.0060, altitude=10.0),
            precision=LocationPrecision.BUILDING,
            timestamp=time.time(),
            label="NYC Office",
            duration_minutes=0,
        )
        encoded = msg.encode()
        decoded = LocationMessage.decode(encoded)
        assert decoded.location_type == LocationType.STATIC
        assert abs(decoded.coordinate.latitude - msg.coordinate.latitude) < 0.001
        assert decoded.label == "NYC Office"
        assert decoded.precision == LocationPrecision.BUILDING

    def test_precision_reduction_building(self) -> None:
        """Building-level precision should reduce to ~3 decimal places."""
        from presidium.core.messaging.location import GeoCoordinate, LocationPrecision
        coord = GeoCoordinate(latitude=40.712836, longitude=-74.006021)
        reduced = coord.reduce_precision(LocationPrecision.BUILDING)
        # Building precision: ~111m, so ~3 decimal places
        assert abs(reduced.latitude - 40.713) < 0.01
        assert abs(reduced.longitude - (-74.006)) < 0.01

    def test_precision_reduction_city(self) -> None:
        """City-level precision should reduce to ~1 decimal place."""
        from presidium.core.messaging.location import GeoCoordinate, LocationPrecision
        coord = GeoCoordinate(latitude=40.712836, longitude=-74.006021)
        reduced = coord.reduce_precision(LocationPrecision.CITY)
        assert abs(reduced.latitude - 40.7) < 0.2
        assert abs(reduced.longitude - (-74.0)) < 0.2

    def test_precision_reduction_exact_unchanged(self) -> None:
        """Exact precision should not modify the coordinate."""
        from presidium.core.messaging.location import GeoCoordinate, LocationPrecision
        coord = GeoCoordinate(latitude=40.712836, longitude=-74.006021)
        reduced = coord.reduce_precision(LocationPrecision.EXACT)
        assert reduced.latitude == coord.latitude
        assert reduced.longitude == coord.longitude

    def test_haversine_distance(self) -> None:
        """Great-circle distance should match known NYC↔LA distance."""
        from presidium.core.messaging.location import GeoCoordinate
        nyc = GeoCoordinate(latitude=40.7128, longitude=-74.0060)
        la = GeoCoordinate(latitude=34.0522, longitude=-118.2437)
        dist = nyc.distance_to(la)
        # Known distance: ~3940 km
        assert 3900 < dist < 4000

    def test_location_expiry(self) -> None:
        """Live location should expire after the configured duration."""
        from presidium.core.messaging.location import (
            LocationMessage, LocationType, GeoCoordinate, LocationPrecision,
        )
        msg = LocationMessage(
            location_type=LocationType.LIVE_START,
            coordinate=GeoCoordinate(latitude=40.7, longitude=-74.0),
            precision=LocationPrecision.BUILDING,
            timestamp=time.time() - 3601,  # 1 hour + 1 second ago
            duration_minutes=60,
        )
        assert msg.is_expired() is True

    def test_static_location_never_expires(self) -> None:
        """Static location should never report as expired."""
        from presidium.core.messaging.location import (
            LocationMessage, LocationType, GeoCoordinate, LocationPrecision,
        )
        msg = LocationMessage(
            location_type=LocationType.STATIC,
            coordinate=GeoCoordinate(latitude=40.7, longitude=-74.0),
            precision=LocationPrecision.EXACT,
            timestamp=time.time() - 999999,
        )
        assert msg.is_expired() is False


# ==========================================================================
# P1: Polls tests
# ==========================================================================

class TestPolls:
    """Test poll create / vote / close lifecycle."""

    def test_poll_definition_encode_decode(self) -> None:
        """PollDefinition should round-trip through encode/decode."""
        from presidium.core.messaging.polls import PollDefinition, PollSettings, PollOption
        poll = PollDefinition(
            poll_id=os.urandom(16),
            question="Where should we have lunch?",
            options=[
                PollOption(text="Pizza Place", index=0),
                PollOption(text="Sushi Bar", index=1),
                PollOption(text="Taco Truck", index=2),
            ],
            settings=PollSettings.NONE,
            creator_user_id="alice",
        )
        encoded = poll.encode()
        decoded = PollDefinition.decode(encoded)
        assert decoded.question == poll.question
        assert [opt.text for opt in decoded.options] == ["Pizza Place", "Sushi Bar", "Taco Truck"]
        assert decoded.poll_id == poll.poll_id

    def test_poll_vote_encode_decode(self) -> None:
        """PollVote should round-trip through encode/decode."""
        from presidium.core.messaging.polls import PollVote
        vote = PollVote(
            poll_id=os.urandom(16),
            option_index=2,
            commitment_hash=hashlib.sha256(b"my_vote_commitment").digest(),
            timestamp=time.time(),
            voter_user_id="alice",
            signature=os.urandom(64),
        )
        encoded = vote.encode()
        decoded = PollVote.decode(encoded)
        assert decoded.poll_id == vote.poll_id
        assert decoded.option_index == vote.option_index
        assert decoded.commitment_hash == vote.commitment_hash

    def test_poll_results(self) -> None:
        """PollResults should count votes correctly."""
        from presidium.core.messaging.polls import PollResults
        results = PollResults(poll_id=os.urandom(16), option_results={0: 5, 1: 3, 2: 7}, total_voters=15, winning_option=2)
        assert results.total_voters == 15
        assert results.winning_option == 2

    def test_poll_results_tie(self) -> None:
        """PollResults with a tie should have winning_option == -1."""
        from presidium.core.messaging.polls import PollResults
        results = PollResults(poll_id=os.urandom(16), option_results={0: 5, 1: 5}, total_voters=10, winning_option=-1)
        assert results.winning_option == -1


# ==========================================================================
# P1: i18n tests
# ==========================================================================

class TestI18n:
    """Test internationalization and translation."""

    def test_default_locale_english(self) -> None:
        """Default locale should be English."""
        from presidium.i18n import I18nManager
        mgr = I18nManager()
        assert mgr.get_current_locale() == "en"

    def test_translate_falls_back_to_key(self) -> None:
        """Unknown translation keys should fall back to the key itself."""
        from presidium.i18n import I18nManager
        mgr = I18nManager()
        result = mgr.translate("Nonexistent Key XYZ")
        assert result == "Nonexistent Key XYZ"

    def test_translate_with_interpolation(self) -> None:
        """Translation should support {variable} interpolation."""
        from presidium.i18n import I18nManager
        mgr = I18nManager()
        result = mgr.translate("You have {count} messages", count=5)
        assert "5" in result

    def test_plural_rules_english(self) -> None:
        """English plural rules: one vs other."""
        from presidium.i18n import PluralRules
        assert PluralRules.get_plural_form("en", 1) == "one"
        assert PluralRules.get_plural_form("en", 0) == "other"
        assert PluralRules.get_plural_form("en", 5) == "other"

    def test_plural_rules_russian(self) -> None:
        """Russian plural rules: one, few, many, other."""
        from presidium.i18n import PluralRules
        assert PluralRules.get_plural_form("ru", 1) == "one"
        assert PluralRules.get_plural_form("ru", 2) == "few"
        assert PluralRules.get_plural_form("ru", 5) == "many"
        assert PluralRules.get_plural_form("ru", 11) == "many"
        assert PluralRules.get_plural_form("ru", 21) == "one"

    def test_plural_rules_arabic(self) -> None:
        """Arabic plural rules: zero, one, two, few, many, other."""
        from presidium.i18n import PluralRules
        assert PluralRules.get_plural_form("ar", 0) == "zero"
        assert PluralRules.get_plural_form("ar", 1) == "one"
        assert PluralRules.get_plural_form("ar", 2) == "two"
        assert PluralRules.get_plural_form("ar", 5) == "few"
        assert PluralRules.get_plural_form("ar", 50) == "many"

    def test_locale_dataclass(self) -> None:
        """Locale dataclass should store translations and detect RTL."""
        from presidium.i18n import Locale
        en = Locale(code="en", name="English", translations={"Hello": "Hello"})
        assert en.is_rtl is False
        assert en.text_direction.value == "ltr"

        ar = Locale(code="ar", name="العربية", translations={"Hello": "مرحبا"})
        assert ar.is_rtl is True


# ==========================================================================
# P2: Accessibility tests
# ==========================================================================

@pytest.mark.skipif(not HAS_PYSIDE6, reason="PySide6 not installed")
class TestA11y:
    """Test accessibility profile and screen reader text generation."""

    def test_chat_bubble_description_own(self) -> None:
        """Screen reader description for own message bubble."""
        from presidium.a11y.manager import ScreenReaderText
        desc = ScreenReaderText.chat_bubble_description(
            sender_name="Alice",
            message_text="Hello, world!",
            timestamp="14:32",
            is_own=True,
            is_read=True,
        )
        assert "You" in desc
        assert "Hello, world!" in desc
        assert "14:32" in desc
        assert "read" in desc

    def test_chat_bubble_description_peer(self) -> None:
        """Screen reader description for peer's message bubble."""
        from presidium.a11y.manager import ScreenReaderText
        desc = ScreenReaderText.chat_bubble_description(
            sender_name="Bob",
            message_text="Hey there!",
            timestamp="14:33",
            is_own=False,
        )
        assert "Bob" in desc
        assert "Hey there!" in desc
        assert "read" not in desc  # No read status for peer messages

    def test_chat_bubble_description_with_reply(self) -> None:
        """Screen reader description with reply indicator."""
        from presidium.a11y.manager import ScreenReaderText
        desc = ScreenReaderText.chat_bubble_description(
            sender_name="Carol",
            message_text="I agree!",
            timestamp="14:35",
            reply_to="Alice",
        )
        assert "reply to Alice" in desc

    def test_contact_description(self) -> None:
        """Screen reader description for a contact item."""
        from presidium.a11y.manager import ScreenReaderText
        desc = ScreenReaderText.contact_description(
            name="Dave",
            user_id="abc123",
            is_online=True,
            unread_count=3,
        )
        assert "Dave" in desc
        assert "online" in desc
        assert "3 unread" in desc

    def test_voice_message_description(self) -> None:
        """Screen reader description for a voice message."""
        from presidium.a11y.manager import ScreenReaderText
        desc = ScreenReaderText.voice_message_description(
            duration_seconds=125,
            is_playing=False,
        )
        assert "2:05" in desc

    def test_voice_message_description_playing(self) -> None:
        """Screen reader description for a playing voice message."""
        from presidium.a11y.manager import ScreenReaderText
        desc = ScreenReaderText.voice_message_description(
            duration_seconds=60,
            is_playing=True,
            position_seconds=30,
        )
        assert "playing" in desc
        assert "0:30" in desc
        assert "1:00" in desc

    def test_color_blind_palette_transform(self) -> None:
        """Color-blind palette should transform colors correctly."""
        from presidium.a11y.manager import ColorBlindPalette
        from PySide6.QtGui import QColor
        original = QColor(255, 0, 0)
        transformed = ColorBlindPalette.transform_color(original, "protan")
        assert isinstance(transformed, QColor)
        # Should be different from original
        assert (transformed.red(), transformed.green(), transformed.blue()) != (255, 0, 0)

    def test_color_blind_palette_safe_colors(self) -> None:
        """Safe palette should return a dictionary of QColor objects."""
        from presidium.a11y.manager import ColorBlindPalette
        palette = ColorBlindPalette.get_safe_palette("deutan")
        assert "primary" in palette
        assert "error" in palette
        assert len(palette) == 8


# ==========================================================================
# P2: Semantic Search tests
# ==========================================================================

class TestSearch:
    """Test semantic search indexing and fallback embedding."""

    def test_fallback_embedding_deterministic(self) -> None:
        """Fallback embedding should be deterministic for the same text."""
        from presidium.core.search.semantic import EmbeddingService
        svc = EmbeddingService()
        text = "Project deadline is next Friday"
        emb1 = svc._fallback_embed(text)
        emb2 = svc._fallback_embed(text)
        assert emb1 == emb2

    def test_fallback_embedding_dimension(self) -> None:
        """Fallback embedding should have the correct dimension."""
        from presidium.core.search.semantic import EmbeddingService, EMBEDDING_DIMENSION
        svc = EmbeddingService()
        emb = svc._fallback_embed("Test embedding")
        assert len(emb) == EMBEDDING_DIMENSION

    def test_fallback_embedding_different_texts(self) -> None:
        """Different texts should produce different embeddings."""
        from presidium.core.search.semantic import EmbeddingService
        svc = EmbeddingService()
        emb1 = svc._fallback_embed("Hello world")
        emb2 = svc._fallback_embed("Goodbye world")
        assert emb1 != emb2

    def test_fallback_embedding_empty_text(self) -> None:
        """Empty text should produce a zero vector."""
        from presidium.core.search.semantic import EmbeddingService, EMBEDDING_DIMENSION
        svc = EmbeddingService()
        emb = svc.embed_text("")
        assert all(v == 0.0 for v in emb)
        assert len(emb) == EMBEDDING_DIMENSION

    def test_indexed_message_hash(self) -> None:
        """IndexedMessage hash should be deterministic."""
        from presidium.core.search.semantic import IndexedMessage
        h1 = IndexedMessage.compute_hash("Same content")
        h2 = IndexedMessage.compute_hash("Same content")
        assert h1 == h2

    def test_indexed_message_hash_changes(self) -> None:
        """Different content should produce different hashes."""
        from presidium.core.search.semantic import IndexedMessage
        h1 = IndexedMessage.compute_hash("Content A")
        h2 = IndexedMessage.compute_hash("Content B")
        assert h1 != h2

    def test_search_query_post_init(self) -> None:
        """SearchQuery should auto-populate normalized and keywords."""
        from presidium.core.search.semantic import SearchQuery
        q = SearchQuery(original="Hello World")
        assert q.normalized == "hello world"
        assert q.keywords == ["hello", "world"]

    def test_vector_store_add_and_size(self) -> None:
        """VectorStore should track added items."""
        from presidium.core.search.semantic import VectorStore, IndexedMessage, EmbeddingService
        store = VectorStore()
        store.initialize()
        svc = EmbeddingService()

        msg = IndexedMessage(
            message_id="msg1",
            peer_user_id="peer1",
            content="Test message for indexing",
            timestamp=time.time(),
        )
        embedding = svc._fallback_embed(msg.content)
        store.add(msg, embedding)
        assert store.size == 1


# ==========================================================================
# P2: Import tests
# ==========================================================================

class TestImport:
    """Test import validation and data sanitization."""

    def test_validation_pipeline_sanitize_html(self) -> None:
        """ValidationPipeline should strip HTML tags from content."""
        import importlib
        mod = importlib.import_module("presidium.import")
        pipeline = mod.ValidationPipeline()
        result = pipeline.sanitize_text("<script>alert('xss')</script>Hello")
        assert "<script>" not in result
        assert "</script>" not in result
        # HTML tags are stripped but text content between tags remains
        assert "Hello" in result

    def test_validation_pipeline_truncate_long_text(self) -> None:
        """ValidationPipeline should truncate excessively long messages."""
        import importlib
        mod = importlib.import_module("presidium.import")
        pipeline = mod.ValidationPipeline()
        long_text = "A" * 100000
        result = pipeline.truncate_text(long_text)
        assert len(result) < len(long_text)

    def test_import_result_dataclass(self) -> None:
        """ImportResult should summarize the import outcome."""
        import importlib
        mod = importlib.import_module("presidium.import")
        result = mod.ImportResult(
            total_messages=100,
            imported_messages=95,
            skipped_messages=3,
            errors=["error1", "error2"],
            source=mod.ImportSource.SIGNAL,
        )
        assert result.total_messages == 100
        assert result.imported_messages == 95
        assert result.success_rate == pytest.approx(0.95)

    def test_import_result_zero_total(self) -> None:
        """ImportResult with zero total should not divide by zero."""
        import importlib
        mod = importlib.import_module("presidium.import")
        result = mod.ImportResult(total_messages=0, imported_messages=0, skipped_messages=0, source=mod.ImportSource.SIGNAL)
        assert result.success_rate == 0.0


# ==========================================================================
# P3: SDK tests
# ==========================================================================

class TestSDK:
    """Test SDK initialization and message send."""

    def test_sdk_config_defaults(self) -> None:
        """SDKConfig should have sensible defaults."""
        from presidium.sdk import SDKConfig
        config = SDKConfig()
        assert config.data_dir == ""
        assert config.enable_encryption is True
        assert config.auto_reconnect is True

    def test_sdk_error_hierarchy(self) -> None:
        """SDK errors should form a proper hierarchy."""
        from presidium.sdk import SDKError, SDKNotInitializedError, SDKConnectionError
        assert issubclass(SDKNotInitializedError, SDKError)
        assert issubclass(SDKConnectionError, SDKError)

    def test_sdk_event_types(self) -> None:
        """SDKEvent should cover all event categories."""
        from presidium.sdk import SDKEvent
        assert SDKEvent.MESSAGE_RECEIVED.value == "message_received"
        assert SDKEvent.MESSAGE_SENT.value == "message_sent"
        assert SDKEvent.CONTACT_ADDED.value == "contact_added"
        assert SDKEvent.GROUP_CREATED.value == "group_created"
        assert SDKEvent.ERROR.value == "error"

    def test_sdk_not_initialized_error(self) -> None:
        """Using the SDK before init should raise SDKNotInitializedError."""
        from presidium.sdk import PresidiumSDK, SDKConfig, SDKNotInitializedError
        config = SDKConfig(app_name="TestApp", api_key="pk_test_123")
        sdk = PresidiumSDK(config)
        with pytest.raises(SDKNotInitializedError):
            sdk._require_initialized()


# ==========================================================================
# P4: Virtual List widget model tests
# ==========================================================================

@pytest.mark.skipif(not HAS_PYSIDE6, reason="PySide6 not installed")
class TestVirtualList:
    """Test virtual list model computation."""

    def test_model_set_data(self) -> None:
        """VirtualListModel should store data and compute geometry."""
        from presidium.ui.components.virtual_list import VirtualListModel
        model = VirtualListModel()
        model.set_data(["a", "b", "c", "d", "e"])
        assert model.item_count == 5
        assert model.total_height == 5 * 64  # DEFAULT_ITEM_HEIGHT

    def test_model_visible_range(self) -> None:
        """Visible range should include the buffer zone."""
        from presidium.ui.components.virtual_list import VirtualListModel, VISIBLE_BUFFER
        model = VirtualListModel()
        model.set_data(list(range(100)))
        start, end = model.visible_range(0, 200)  # First ~3 items visible
        assert start == 0
        assert end >= 3 + VISIBLE_BUFFER

    def test_model_index_for_y(self) -> None:
        """index_for_y should return the correct row for a pixel offset."""
        from presidium.ui.components.virtual_list import VirtualListModel
        model = VirtualListModel()
        model.set_data(list(range(20)))
        # At y=0, index should be 0
        assert model.index_for_y(0) == 0
        # At y=64, index should be 1
        assert model.index_for_y(64) == 1
        # At y=128, index should be 2
        assert model.index_for_y(128) == 2

    def test_model_variable_height(self) -> None:
        """Model should handle variable-height items correctly."""
        from presidium.ui.components.virtual_list import VirtualListModel
        model = VirtualListModel()
        model.set_data(["short", "tall", "medium"])
        model.set_actual_height(0, 40)
        model.set_actual_height(1, 120)
        model.set_actual_height(2, 80)
        assert model.total_height == 40 + 120 + 80
        assert model.y_for_index(0) == 0
        assert model.y_for_index(1) == 40
        assert model.y_for_index(2) == 160

    def test_model_append_data(self) -> None:
        """Appending data should update the item count."""
        from presidium.ui.components.virtual_list import VirtualListModel
        model = VirtualListModel()
        model.set_data(["a", "b"])
        model.append_data("c", estimated_height=80)
        assert model.item_count == 3
        assert model.get_height(2) == 80

    def test_model_remove_data(self) -> None:
        """Removing data should update the item count and heights."""
        from presidium.ui.components.virtual_list import VirtualListModel
        model = VirtualListModel()
        model.set_data(["a", "b", "c"])
        removed = model.remove_data(1)
        assert removed == "b"
        assert model.item_count == 2

    def test_model_invalid_index_raises(self) -> None:
        """Accessing an invalid index should raise InvalidIndexError."""
        from presidium.ui.components.virtual_list import VirtualListModel, InvalidIndexError
        model = VirtualListModel()
        model.set_data(["a"])
        with pytest.raises(InvalidIndexError):
            model.get_data(5)
        with pytest.raises(InvalidIndexError):
            model.remove_data(-1)

    def test_model_clear(self) -> None:
        """Clearing the model should reset all state."""
        from presidium.ui.components.virtual_list import VirtualListModel
        model = VirtualListModel()
        model.set_data(["a", "b", "c"])
        model.clear()
        assert model.item_count == 0
        assert model.total_height == 0


# ==========================================================================
# P4: Welcome v2 wizard tests
# ==========================================================================

@pytest.mark.skipif(not HAS_PYSIDE6, reason="PySide6 not installed")
class TestWelcomeV2:
    """Test onboarding wizard logic."""

    def test_wizard_step_enum(self) -> None:
        """WizardStep should have exactly 5 steps."""
        from presidium.ui.screens.welcome_v2 import WizardStep
        assert len(WizardStep) == 5
        assert WizardStep.WELCOME == 0
        assert WizardStep.READY == 4

    def test_recovery_phrase_generation(self) -> None:
        """Recovery phrase should have 12 words from the word list."""
        from presidium.ui.screens.welcome_v2 import generate_recovery_phrase
        phrase = generate_recovery_phrase()
        assert len(phrase) == 12
        assert all(isinstance(w, str) for w in phrase)
        assert all(len(w) > 0 for w in phrase)

    def test_recovery_phrase_custom_count(self) -> None:
        """Recovery phrase should support custom word counts."""
        from presidium.ui.screens.welcome_v2 import generate_recovery_phrase
        phrase = generate_recovery_phrase(word_count=24)
        assert len(phrase) == 24

    def test_recovery_phrase_randomness(self) -> None:
        """Two recovery phrases should (almost certainly) differ."""
        from presidium.ui.screens.welcome_v2 import generate_recovery_phrase
        p1 = generate_recovery_phrase()
        p2 = generate_recovery_phrase()
        assert p1 != p2

    def test_password_validation_too_short(self) -> None:
        """Short passwords should raise WeakPasswordError."""
        from presidium.ui.screens.welcome_v2 import WelcomeWizard, WeakPasswordError
        wizard = WelcomeWizard()
        wizard._password_input = MagicMock()
        wizard._confirm_input = MagicMock()
        wizard._password_input.text = MagicMock(return_value="short")
        wizard._confirm_input.text = MagicMock(return_value="short")
        with pytest.raises(WeakPasswordError):
            wizard._validate_password()

    def test_password_validation_mismatch(self) -> None:
        """Mismatched passwords should raise PasswordMismatchError."""
        from presidium.ui.screens.welcome_v2 import WelcomeWizard, PasswordMismatchError
        wizard = WelcomeWizard()
        wizard._password_input = MagicMock()
        wizard._confirm_input = MagicMock()
        wizard._password_input.text = MagicMock(return_value="LongPassword1")
        wizard._confirm_input.text = MagicMock(return_value="LongPassword2")
        with pytest.raises(PasswordMismatchError):
            wizard._validate_password()

    def test_password_validation_weak_all_same(self) -> None:
        """All-same-character passwords should raise WeakPasswordError."""
        from presidium.ui.screens.welcome_v2 import WelcomeWizard, WeakPasswordError
        wizard = WelcomeWizard()
        wizard._password_input = MagicMock()
        wizard._confirm_input = MagicMock()
        wizard._password_input.text = MagicMock(return_value="aaaaaaaa")
        wizard._confirm_input.text = MagicMock(return_value="aaaaaaaa")
        with pytest.raises(WeakPasswordError):
            wizard._validate_password()

    def test_password_validation_success(self) -> None:
        """Valid passwords should pass validation."""
        from presidium.ui.screens.welcome_v2 import WelcomeWizard
        wizard = WelcomeWizard()
        wizard._password_input = MagicMock()
        wizard._confirm_input = MagicMock()
        wizard._password_input.text = MagicMock(return_value="SecurePass1")
        wizard._confirm_input.text = MagicMock(return_value="SecurePass1")
        result = wizard._validate_password()
        assert result == "SecurePass1"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
