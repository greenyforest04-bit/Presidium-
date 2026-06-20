"""Full integration test for Presidium.

Scenario:
1. Node A creates identity
2. Node B creates identity
3. A ↔ B Double Ratchet session establishment
4. Text message exchange (10 messages each direction)
5. Disappearing messages
6. Identity lock/unlock
7. Crypto round-trips
"""

from __future__ import annotations

import asyncio
import os
import pytest
import time

from presidium.core.crypto import Ed25519KeyPair, X25519KeyPair, compute_user_id, AES256GCM
from presidium.core.messaging.ratchet import DoubleRatchet
from presidium.core.messaging.disappearing import DisappearingTTL, DisappearingMessageScheduler


# ---------------------------------------------------------------------------
# Test: Double Ratchet full bidirectional exchange
# ---------------------------------------------------------------------------

class TestFullRatchetFlow:
    def test_initiator_responder_exchange(self) -> None:
        shared_secret = os.urandom(32)
        bob_prekey = X25519KeyPair().public_key_bytes

        alice = DoubleRatchet()
        alice_public = alice.init_as_initiator(shared_secret, bob_prekey)

        bob = DoubleRatchet()
        bob.init_as_responder(shared_secret, alice_public)

        # Alice → Bob: 10 messages
        for i in range(10):
            enc_h, enc_b = alice.encrypt(f"Alice msg {i}".encode())
            assert bob.decrypt(enc_h, enc_b) == f"Alice msg {i}".encode()

        # Bob → Alice: 10 messages (triggers DH ratchet)
        for i in range(10):
            enc_h, enc_b = bob.encrypt(f"Bob msg {i}".encode())
            assert alice.decrypt(enc_h, enc_b) == f"Bob msg {i}".encode()

    def test_out_of_order_recovery(self) -> None:
        shared_secret = os.urandom(32)
        bob_prekey = X25519KeyPair().public_key_bytes

        alice = DoubleRatchet()
        alice_public = alice.init_as_initiator(shared_secret, bob_prekey)
        bob = DoubleRatchet()
        bob.init_as_responder(shared_secret, alice_public)

        msgs = []
        for i in range(5):
            enc_h, enc_b = alice.encrypt(f"Msg {i}".encode())
            msgs.append((enc_h, enc_b, f"Msg {i}".encode()))

        # Deliver in reverse order
        for enc_h, enc_b, expected in reversed(msgs):
            assert bob.decrypt(enc_h, enc_b) == expected


# ---------------------------------------------------------------------------
# Test: Disappearing messages
# ---------------------------------------------------------------------------

class TestDisappearingMessages:
    @pytest.mark.asyncio
    async def test_message_expires_after_ttl(self) -> None:
        deleted = []

        async def on_delete(mid: str, _: str) -> None:
            deleted.append(mid)

        scheduler = DisappearingMessageScheduler(on_delete, secure_wipe=False)
        await scheduler.start()
        scheduler.schedule("msg_001", "peer_a", ttl=2)
        await asyncio.sleep(3)
        assert "msg_001" in deleted
        await scheduler.stop()


# ---------------------------------------------------------------------------
# Test: Identity creation + lock/unlock
# ---------------------------------------------------------------------------

class TestIdentityCreation:
    @pytest.mark.asyncio
    async def test_create_lock_unlock(self) -> None:
        from presidium.core.identity import IdentityManager, WrongPasswordError
        from presidium.utils.config import AppConfig
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig(data_dir=tmpdir)
            mgr = IdentityManager(config)
            assert not mgr.is_initialized

            uid = await mgr.create_identity("MyPass123!", "TestNode")
            assert len(uid) == 32
            assert mgr.is_unlocked

            mgr.lock()
            assert not mgr.is_unlocked

            uid2 = await mgr.unlock("MyPass123!")
            assert uid2 == uid

            mgr.lock()
            with pytest.raises(WrongPasswordError):
                await mgr.unlock("wrong!")


# ---------------------------------------------------------------------------
# Test: Crypto primitives
# ---------------------------------------------------------------------------

class TestCryptoPrimitives:
    def test_ed25519_sign_verify(self) -> None:
        kp = Ed25519KeyPair()
        sig = kp.sign(b"data")
        assert Ed25519KeyPair.verify(kp.public_key_bytes, sig, b"data")
        assert not Ed25519KeyPair.verify(kp.public_key_bytes, sig, b"other")

    def test_x25519_ecdh(self) -> None:
        a, b = X25519KeyPair(), X25519KeyPair()
        assert bytes(a.shared_secret(b.public_key_bytes)) == bytes(b.shared_secret(a.public_key_bytes))

    def test_aes256gcm_aad(self) -> None:
        key = os.urandom(32)
        ct = AES256GCM.encrypt(key, b"secret", aad=b"ctx")
        assert AES256GCM.decrypt(key, ct, aad=b"ctx") == b"secret"

    def test_user_id_deterministic(self) -> None:
        pk = os.urandom(32)
        assert compute_user_id(pk) == compute_user_id(pk)
