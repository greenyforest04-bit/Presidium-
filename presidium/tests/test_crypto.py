"""Tests for the crypto module.

Tests:
- Ed25519 signing and verification
- X25519 key exchange
- AES-256-GCM encryption/decryption
- Argon2id key derivation
- SecureBytes zeroing
- User ID computation
- Identity management
"""

from __future__ import annotations

import os
import pytest

from presidium.core.crypto import (
    Ed25519KeyPair,
    X25519KeyPair,
    AES256GCM,
    SecureBytes,
    CryptoError,
    EncryptionError,
    DecryptionError,
    KeyDerivationError,
    compute_user_id,
    derive_key_from_password,
    generate_salt,
    advance_chain_key,
    secure_zero,
)


class TestEd25519:
    def test_sign_and_verify(self) -> None:
        kp = Ed25519KeyPair()
        data = b"test message"
        sig = kp.sign(data)
        assert len(sig) == 64
        assert Ed25519KeyPair.verify(kp.public_key_bytes, sig, data)

    def test_wrong_data_fails_verify(self) -> None:
        kp = Ed25519KeyPair()
        sig = kp.sign(b"correct data")
        assert not Ed25519KeyPair.verify(kp.public_key_bytes, sig, b"wrong data")

    def test_wrong_key_fails_verify(self) -> None:
        kp1 = Ed25519KeyPair()
        kp2 = Ed25519KeyPair()
        sig = kp1.sign(b"data")
        assert not Ed25519KeyPair.verify(kp2.public_key_bytes, sig, b"data")

    def test_public_key_bytes_length(self) -> None:
        kp = Ed25519KeyPair()
        assert len(kp.public_key_bytes) == 32

    def test_encrypted_pem_roundtrip(self) -> None:
        kp = Ed25519KeyPair()
        password = "test_password_123"
        encrypted = kp.to_encrypted_pem(password)
        assert len(encrypted) > 32  # salt + PEM
        restored = Ed25519KeyPair.from_encrypted_pem(encrypted, password)
        assert restored.public_key_bytes == kp.public_key_bytes

    def test_wrong_password_fails(self) -> None:
        kp = Ed25519KeyPair()
        encrypted = kp.to_encrypted_pem("correct_password")
        with pytest.raises(CryptoError):
            Ed25519KeyPair.from_encrypted_pem(encrypted, "wrong_password")


class TestX25519:
    def test_shared_secret_consistency(self) -> None:
        alice = X25519KeyPair()
        bob = X25519KeyPair()
        alice_secret = alice.shared_secret(bob.public_key_bytes)
        bob_secret = bob.shared_secret(alice.public_key_bytes)
        assert bytes(alice_secret) == bytes(bob_secret)

    def test_different_peers_different_secrets(self) -> None:
        alice = X25519KeyPair()
        bob = X25519KeyPair()
        charlie = X25519KeyPair()
        ab = alice.shared_secret(bob.public_key_bytes)
        ac = alice.shared_secret(charlie.public_key_bytes)
        assert bytes(ab) != bytes(ac)

    def test_public_key_bytes_length(self) -> None:
        kp = X25519KeyPair()
        assert len(kp.public_key_bytes) == 32


class TestAES256GCM:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        key = os.urandom(32)
        plaintext = b"Hello, World! This is a test message."
        encrypted = AES256GCM.encrypt(key, plaintext)
        decrypted = AES256GCM.decrypt(key, encrypted)
        assert decrypted == plaintext

    def test_with_aad(self) -> None:
        key = os.urandom(32)
        plaintext = b"secret data"
        aad = b"additional data"
        encrypted = AES256GCM.encrypt(key, plaintext, aad=aad)
        decrypted = AES256GCM.decrypt(key, encrypted, aad=aad)
        assert decrypted == plaintext

    def test_wrong_aad_fails(self) -> None:
        key = os.urandom(32)
        plaintext = b"secret data"
        encrypted = AES256GCM.encrypt(key, plaintext, aad=b"correct aad")
        with pytest.raises(DecryptionError):
            AES256GCM.decrypt(key, encrypted, aad=b"wrong aad")

    def test_wrong_key_fails(self) -> None:
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        encrypted = AES256GCM.encrypt(key1, b"secret")
        with pytest.raises(DecryptionError):
            AES256GCM.decrypt(key2, encrypted)

    def test_tampered_ciphertext_fails(self) -> None:
        key = os.urandom(32)
        encrypted = AES256GCM.encrypt(key, b"secret")
        # Tamper with ciphertext
        tampered = encrypted[:20] + bytes([(encrypted[20] + 1) % 256]) + encrypted[21:]
        with pytest.raises(DecryptionError):
            AES256GCM.decrypt(key, tampered)

    def test_invalid_key_length(self) -> None:
        with pytest.raises(EncryptionError):
            AES256GCM.encrypt(os.urandom(16), b"test")

    def test_too_short_ciphertext(self) -> None:
        with pytest.raises(DecryptionError, match="too short"):
            AES256GCM.decrypt(os.urandom(32), os.urandom(20))

    def test_secure_bytes_key(self) -> None:
        key = SecureBytes(os.urandom(32))
        plaintext = b"test with SecureBytes key"
        encrypted = AES256GCM.encrypt(key, plaintext)
        decrypted = AES256GCM.decrypt(key, encrypted)
        assert decrypted == plaintext


class TestSecureBytes:
    def test_bytes_conversion(self) -> None:
        data = b"hello"
        sb = SecureBytes(data)
        assert bytes(sb) == data

    def test_len(self) -> None:
        sb = SecureBytes(b"hello")
        assert len(sb) == 5

    def test_repr(self) -> None:
        sb = SecureBytes(b"hello")
        assert "SecureBytes" in repr(sb)
        assert "5" in repr(sb)

    def test_explicit_zero(self) -> None:
        sb = SecureBytes(b"secret data that should be zeroed")
        sb.zero()
        # After zeroing, the internal data should be all zeros
        assert bytes(sb) == b"\x00" * len(sb)


class TestArgon2:
    def test_derive_key(self) -> None:
        salt = generate_salt(32)
        key = derive_key_from_password("test_password", salt)
        assert len(key) == 32

    def test_same_inputs_same_output(self) -> None:
        salt = generate_salt(32)
        key1 = derive_key_from_password("password", salt)
        key2 = derive_key_from_password("password", salt)
        assert bytes(key1) == bytes(key2)

    def test_different_passwords_different_keys(self) -> None:
        salt = generate_salt(32)
        key1 = derive_key_from_password("password1", salt)
        key2 = derive_key_from_password("password2", salt)
        assert bytes(key1) != bytes(key2)

    def test_short_salt_raises(self) -> None:
        with pytest.raises(KeyDerivationError, match="at least 16"):
            derive_key_from_password("password", b"short")


class TestUserID:
    def test_deterministic(self) -> None:
        pk = os.urandom(32)
        uid1 = compute_user_id(pk)
        uid2 = compute_user_id(pk)
        assert uid1 == uid2

    def test_format(self) -> None:
        pk = os.urandom(32)
        uid = compute_user_id(pk)
        assert len(uid) == 32
        assert all(c in "0123456789abcdef" for c in uid)

    def test_different_keys_different_ids(self) -> None:
        pk1 = os.urandom(32)
        pk2 = os.urandom(32)
        assert compute_user_id(pk1) != compute_user_id(pk2)


class TestChainKeyAdvancement:
    def test_deterministic(self) -> None:
        ck = os.urandom(32)
        new1, mk1 = advance_chain_key(ck)
        new2, mk2 = advance_chain_key(ck)
        assert new1 == new2
        assert mk1 == mk2

    def test_different_from_input(self) -> None:
        ck = os.urandom(32)
        new_ck, msg_key = advance_chain_key(ck)
        assert new_ck != ck
        assert msg_key != ck
        assert new_ck != msg_key
