"""Cryptographic primitives for Presidium.

Provides:
- X25519 key exchange (ephemeral + long-term)
- Ed25519 digital signatures
- AES-256-GCM authenticated encryption
- Argon2id key derivation from user password
- Secure memory zeroing for sensitive key material
- Constant-time comparison for MAC/signature verification

Security audit notes:
- All MAC/signature comparisons use hmac.compare_digest (constant-time)
- SecureBytes uses ctypes.memset for zeroing + mlock/munlock on Linux
- os.urandom is used ONLY for cryptographic purposes
- Non-cryptographic random uses secrets.token_bytes
- Argon2id parameters configurable via PRESIDIUM_ARGON_* env vars
"""

from __future__ import annotations

import ctypes
import hmac
import logging
import os
import secrets
import struct
import sys
from typing import Optional

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from argon2.low_level import hash_secret_raw, Type

from presidium.utils.config import CryptoConfig


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class CryptoError(Exception):
    """Base exception for cryptographic operations."""


class KeyDerivationError(CryptoError):
    """Failed to derive key from password."""


class EncryptionError(CryptoError):
    """Failed to encrypt data."""


class DecryptionError(CryptoError):
    """Failed to decrypt data."""


class SignatureError(CryptoError):
    """Failed to sign or verify data."""


# ---------------------------------------------------------------------------
# Secure memory zeroing
# ---------------------------------------------------------------------------

# Platform detection for mlock/munlock
_LINUX = sys.platform == "linux"
_libc = None
if _LINUX:
    try:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
        _libc.mlock.restype = ctypes.c_int
        _libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _libc.munlock.restype = ctypes.c_int
        _libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    except OSError:
        _libc = None

logger = logging.getLogger(__name__)


def secure_zero(buffer: bytearray | memoryview) -> None:
    """Overwrite a bytearray with zeros using ctypes.memset.

    This is a best-effort approach; Python's memory model makes true
    guarantees impossible, but we avoid the optimiser eliding the write.
    On Linux, also calls munlock to release any mlock'd pages.
    """
    if isinstance(buffer, memoryview):
        # memoryview cannot be overwritten directly via memset safely;
        # create a temporary mutable view if the underlying object is writable.
        if buffer.obj is not None and hasattr(buffer.obj, "__setitem__"):
            for i in range(len(buffer)):
                buffer[i] = 0
        return
    addr = ctypes.addressof((ctypes.c_char * len(buffer)).from_buffer(buffer))
    ctypes.memset(addr, 0, len(buffer))
    # On Linux, release mlock'd memory after zeroing
    if _LINUX and _libc is not None:
        try:
            _libc.munlock(addr, len(buffer))
        except Exception:
            pass  # Best-effort; may fail if not mlock'd


class SecureBytes:
    """A byte container that zeroes its memory on deletion.

    Uses ctypes.memset for secure zeroing and mlock on Linux
    to prevent the buffer from being swapped to disk.
    """

    def __init__(self, data: bytes | bytearray) -> None:
        self._data = bytearray(data)
        self._locked = False
        # Attempt to mlock the buffer on Linux to prevent swapping
        if _LINUX and _libc is not None:
            try:
                addr = ctypes.addressof(
                    (ctypes.c_char * len(self._data)).from_buffer(self._data)
                )
                if _libc.mlock(addr, len(self._data)) == 0:
                    self._locked = True
            except Exception:
                pass  # Best-effort; mlock may fail due to limits

    def __len__(self) -> int:
        return len(self._data)

    def __bytes__(self) -> bytes:
        return bytes(self._data)

    def __repr__(self) -> str:
        return f"SecureBytes(len={len(self._data)})"

    @property
    def data(self) -> memoryview:
        """Read-only view of the secure bytes."""
        return memoryview(self._data)

    def zero(self) -> None:
        """Explicitly zero the internal buffer and release mlock."""
        secure_zero(self._data)
        self._locked = False

    def __del__(self) -> None:
        try:
            self.zero()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Argon2id key derivation
# ---------------------------------------------------------------------------

def _get_argon2_params() -> tuple[int, int, int]:
    """Get Argon2id parameters from env vars or defaults.

    Environment variables (override CryptoConfig defaults):
        PRESIDIUM_ARGON_MEMORY: Memory cost in KiB (default: 65536 = 64 MB)
        PRESIDIUM_ARGON_ITERATIONS: Time cost / iterations (default: 3)
        PRESIDIUM_ARGON_PARALLELISM: Parallelism (default: 4)

    Returns:
        Tuple of (time_cost, memory_cost, parallelism).
    """
    time_cost = int(os.environ.get("PRESIDIUM_ARGON_ITERATIONS", "3"))
    memory_cost = int(os.environ.get("PRESIDIUM_ARGON_MEMORY", "65536"))
    parallelism = int(os.environ.get("PRESIDIUM_ARGON_PARALLELISM", "4"))

    # Enforce minimums per security audit requirements
    if time_cost < 3:
        logger.warning(
            "PRESIDIUM_ARGON_ITERATIONS=%d is below minimum (3), using 3.",
            time_cost,
        )
        time_cost = 3
    if memory_cost < 65536:
        logger.warning(
            "PRESIDIUM_ARGON_MEMORY=%d KiB is below minimum (65536 = 64 MB), using 65536.",
            memory_cost,
        )
        memory_cost = 65536
    if parallelism < 1:
        logger.warning(
            "PRESIDIUM_ARGON_PARALLELISM=%d is below minimum (1), using 1.",
            parallelism,
        )
        parallelism = 1

    return time_cost, memory_cost, parallelism


def derive_key_from_password(
    password: str,
    salt: bytes,
    config: Optional[CryptoConfig] = None,
    key_length: int = 32,
) -> SecureBytes:
    """Derive a 256-bit key from a user password using Argon2id.

    Args:
        password: The user-provided password.
        salt: Random salt (at least 16 bytes).
        config: CryptoConfig with Argon2 parameters. Uses defaults if None.
            Environment variables PRESIDIUM_ARGON_* take precedence.
        key_length: Output key length in bytes (default 32 for AES-256).

    Returns:
        SecureBytes containing the derived key.

    Raises:
        KeyDerivationError: If key derivation fails.
    """
    # Environment variables take precedence over config
    env_time, env_mem, env_par = _get_argon2_params()

    if config is None:
        config = CryptoConfig()

    # Use env vars if they differ from defaults (user explicitly set them)
    time_cost = env_time
    memory_cost = env_mem
    parallelism = env_par

    if len(salt) < 16:
        raise KeyDerivationError("Salt must be at least 16 bytes.")

    try:
        derived = hash_secret_raw(
            secret=password.encode("utf-8"),
            salt=salt,
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=key_length,
            type=Type.ID,
        )
        return SecureBytes(derived)
    except Exception as exc:
        raise KeyDerivationError(f"Argon2id derivation failed: {exc}") from exc


def generate_salt(size: int = 32) -> bytes:
    """Generate a cryptographically random salt.

    Uses os.urandom which is suitable for cryptographic purposes.
    For non-cryptographic random, use secrets.token_bytes instead.
    """
    return os.urandom(size)


def generate_random_id(size: int = 16) -> bytes:
    """Generate a non-cryptographic random ID.

    Uses secrets.token_bytes for non-cryptographic identifiers
    such as session IDs, poll IDs, notification IDs, etc.
    This keeps os.urandom reserved for cryptographic purposes only.
    """
    return secrets.token_bytes(size)


def constant_time_compare(a: bytes, b: bytes) -> bool:
    """Constant-time byte comparison to prevent timing attacks.

    Uses hmac.compare_digest which is designed to resist timing
    side-channels when comparing MACs, signatures, or other
    security-sensitive byte sequences.

    MUST be used for:
    - MAC/tag verification
    - Signature comparison
    - Recipient hint matching
    - Hash digest comparison
    - Any security-sensitive byte equality check

    MUST NOT be used for:
    - Length checks (len(a) != len(b) is fine)
    - Non-security comparisons
    """
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Ed25519 — Digital Signatures & Identity
# ---------------------------------------------------------------------------

class Ed25519KeyPair:
    """Ed25519 signing key pair for node identity and message authentication."""

    def __init__(self, private_key: Optional[Ed25519PrivateKey] = None) -> None:
        if private_key is None:
            private_key = Ed25519PrivateKey.generate()
        self._private_key = private_key
        self._public_key = private_key.public_key()

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    @property
    def public_key_bytes(self) -> bytes:
        """32-byte raw public key."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def private_key_bytes(self) -> bytes:
        """32-byte raw private key seed."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def sign(self, data: bytes) -> bytes:
        """Sign data and return 64-byte signature."""
        try:
            return self._private_key.sign(data)
        except Exception as exc:
            raise SignatureError(f"Signing failed: {exc}") from exc

    @staticmethod
    def verify(public_key_bytes: bytes, signature: bytes, data: bytes) -> bool:
        """Verify a signature against a public key and data.

        Returns:
            True if the signature is valid, False otherwise.
        """
        try:
            pub = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            pub.verify(signature, data)
            return True
        except Exception:
            return False

    def to_encrypted_pem(self, password: str) -> bytes:
        """Serialize private key to encrypted PEM using Argon2-derived key.

        The PEM uses AES-256-GCM via cryptography's built-in encryption.
        """
        salt = generate_salt()
        # Use cryptography's built-in password-based encryption
        derived_key = derive_key_from_password(password, salt)
        try:
            pem = self._private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.BestAvailableEncryption(bytes(derived_key)),
            )
            # Prepend salt (32 bytes) so we can decrypt later
            return salt + pem
        finally:
            derived_key.zero()

    @classmethod
    def from_encrypted_pem(cls, data: bytes, password: str) -> Ed25519KeyPair:
        """Deserialize private key from encrypted PEM.

        Args:
            data: salt (32 bytes) + PEM-encoded encrypted private key.
            password: User password for decryption.

        Returns:
            Ed25519KeyPair instance.
        """
        if len(data) < 33:
            raise CryptoError("Invalid encrypted key data: too short.")
        salt = data[:32]
        pem_data = data[32:]
        derived_key = derive_key_from_password(password, salt)
        try:
            private_key = serialization.load_pem_private_key(
                pem_data,
                password=bytes(derived_key),
            )
            if not isinstance(private_key, Ed25519PrivateKey):
                raise CryptoError("Loaded key is not Ed25519.")
            return cls(private_key=private_key)
        except CryptoError:
            raise
        except Exception as exc:
            raise CryptoError(f"Failed to decrypt private key (wrong password?): {exc}") from exc
        finally:
            derived_key.zero()


# ---------------------------------------------------------------------------
# X25519 — Key Exchange
# ---------------------------------------------------------------------------

class X25519KeyPair:
    """X25519 ephemeral key pair for ECDH key exchange (Perfect Forward Secrecy)."""

    def __init__(self, private_key: Optional[X25519PrivateKey] = None) -> None:
        if private_key is None:
            private_key = X25519PrivateKey.generate()
        self._private_key = private_key
        self._public_key = private_key.public_key()

    @property
    def public_key(self) -> X25519PublicKey:
        return self._public_key

    @property
    def public_key_bytes(self) -> bytes:
        """32-byte raw X25519 public key."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def shared_secret(self, peer_public_bytes: bytes) -> SecureBytes:
        """Perform X25519 ECDH and return the raw shared secret.

        Args:
            peer_public_bytes: 32-byte raw X25519 public key from the peer.

        Returns:
            SecureBytes containing the 32-byte shared secret.
        """
        try:
            peer_public = X25519PublicKey.from_public_bytes(peer_public_bytes)
            secret = self._private_key.exchange(peer_public)
            return SecureBytes(secret)
        except Exception as exc:
            raise CryptoError(f"X25519 key exchange failed: {exc}") from exc

    def zero_private_key(self) -> None:
        """Attempt to zero the private key from memory."""
        try:
            raw = self._private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            secure_zero(bytearray(raw))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AES-256-GCM — Authenticated Encryption
# ---------------------------------------------------------------------------

class AES256GCM:
    """AES-256-GCM authenticated encryption/decryption."""

    NONCE_SIZE = 12  # 96-bit nonce recommended for GCM
    TAG_SIZE = 16    # 128-bit authentication tag

    @staticmethod
    def encrypt(key: bytes | SecureBytes, plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
        """Encrypt plaintext with AES-256-GCM.

        Args:
            key: 32-byte encryption key.
            plaintext: Data to encrypt.
            aad: Optional additional authenticated data.

        Returns:
            nonce (12 bytes) + ciphertext + tag.
        """
        key_bytes = bytes(key) if isinstance(key, SecureBytes) else key
        if len(key_bytes) != 32:
            raise EncryptionError("AES-256 requires a 32-byte key.")

        nonce = os.urandom(AES256GCM.NONCE_SIZE)
        try:
            aesgcm = AESGCM(key_bytes)
            ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
            return nonce + ciphertext  # ciphertext already includes the tag
        except Exception as exc:
            raise EncryptionError(f"AES-256-GCM encryption failed: {exc}") from exc

    @staticmethod
    def decrypt(key: bytes | SecureBytes, data: bytes, aad: Optional[bytes] = None) -> bytes:
        """Decrypt data encrypted with AES-256-GCM.

        Args:
            key: 32-byte encryption key.
            data: nonce (12 bytes) + ciphertext + tag.
            aad: Optional additional authenticated data.

        Returns:
            Decrypted plaintext.

        Raises:
            DecryptionError: If decryption or authentication fails.
        """
        key_bytes = bytes(key) if isinstance(key, SecureBytes) else key
        if len(key_bytes) != 32:
            raise DecryptionError("AES-256 requires a 32-byte key.")
        if len(data) < AES256GCM.NONCE_SIZE + AES256GCM.TAG_SIZE + 1:
            raise DecryptionError("Ciphertext too short.")

        nonce = data[: AES256GCM.NONCE_SIZE]
        ciphertext = data[AES256GCM.NONCE_SIZE :]
        try:
            aesgcm = AESGCM(key_bytes)
            return aesgcm.decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise DecryptionError(f"AES-256-GCM decryption failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Session Key Derivation (HKDF-SHA256)
# ---------------------------------------------------------------------------

def derive_session_key(
    shared_secret: bytes | SecureBytes,
    info: bytes = b"presidium-session-v1",
    salt: Optional[bytes] = None,
) -> SecureBytes:
    """Derive a 256-bit session key from a shared secret using HKDF-SHA256.

    Args:
        shared_secret: Raw ECDH shared secret from X25519.
        info: Context/application info string.
        salt: Optional salt for HKDF (random if None).

    Returns:
        SecureBytes with the 32-byte session key.
    """
    secret_bytes = bytes(shared_secret) if isinstance(shared_secret, SecureBytes) else shared_secret

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=info,
    )
    derived = hkdf.derive(secret_bytes)
    return SecureBytes(derived)


# ---------------------------------------------------------------------------
# Double Ratchet helpers (simplified)
# ---------------------------------------------------------------------------

def advance_chain_key(chain_key: bytes) -> tuple[bytes, bytes]:
    """Advance a chain key and return (new_chain_key, message_key).

    Uses HKDF with different info strings to derive both outputs from
    the current chain key. This is a simplified version of the Signal
    double ratchet chain advancement.

    Returns:
        (new_chain_key_32bytes, message_key_32bytes)
    """
    hkdf_ck = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"presidium-chain-advance",
    )
    new_chain = hkdf_ck.derive(chain_key)

    hkdf_mk = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"presidium-message-key",
    )
    message_key = hkdf_mk.derive(new_chain)

    return new_chain, message_key


def compute_user_id(public_key_bytes: bytes) -> str:
    """Compute User ID as first 16 bytes of SHA-256(Ed25519 public key), hex-encoded.

    Args:
        public_key_bytes: 32-byte raw Ed25519 public key.

    Returns:
        32-character lowercase hex string.
    """
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public_key_bytes)
    full_hash = digest.finalize()
    return full_hash[:16].hex()
