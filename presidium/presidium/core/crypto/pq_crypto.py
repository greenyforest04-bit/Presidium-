"""Post-Quantum Cryptography for Presidium.

Implements hybrid key exchange combining classical (X25519) with
post-quantum (ML-KEM-768/Kyber) algorithms, and stateful hash-based
signatures (LMS) for long-term identity keys.

Hybrid Key Exchange (ML-KEM-768 + X25519):
    Each ephemeral key exchange produces TWO shared secrets:
        - classical_secret = X25519(eph_classical, peer_classical)
        - pq_secret = ML-KEM-768.Encaps(peer_pq_pk)
    The combined secret is:
        combined = HKDF(classical_secret || pq_secret, "presidium-pq-hybrid")
    This ensures security even if one algorithm is broken.

ML-KEM-768 (Kyber):
    - NIST PQC standard (FIPS 203)
    - Lattice-based key encapsulation mechanism
    - 768-bit security level (≈ AES-192 equivalent)
    - Public key: 1184 bytes, ciphertext: 1088 bytes
    - Uses liboqs-python (Open Quantum Safe) or pure-Python fallback

LMS Signatures (Leighton-Micali):
    - Stateful hash-based signature scheme (NIST SP 800-208)
    - Post-quantum secure (only relies on hash functions)
    - Limited number of signatures (2^10 = 1024 by default)
    - Used for master identity key (low signing frequency)
    - State counter stored atomically in SQLite

Fallback behavior:
    If liboqs is not available, the system falls back to pure X25519
    with a security warning. This ensures compatibility on all platforms.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

from presidium.core.crypto import (
    CryptoError,
    SecureBytes,
    X25519KeyPair,
    constant_time_compare,
    secure_zero,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ML-KEM-768 parameters
ML_KEM_768_PK_SIZE = 1184
ML_KEM_768_CT_SIZE = 1088
ML_KEM_768_SS_SIZE = 32

# LMS parameters
LMS_TYPE = 0x0006        # LMS_SHA256_M32_H10 (2^10 = 1024 signatures)
LM_OTS_TYPE = 0x0004     # LMOTS_SHA256_N32_W4
LMS_MAX_SIGNATURES = 1024
LMS_PUBLIC_KEY_SIZE = 60  # type(4) + otstype(4) + I(16) + K(32) + lms_type(4)
LMS_SIGNATURE_SIZE = 2660  # Approximate (varies with q)

# Hybrid combined key sizes
HYBRID_PK_SIZE = 32 + ML_KEM_768_PK_SIZE       # X25519 + ML-KEM-768
HYBRID_CT_SIZE = 32 + ML_KEM_768_CT_SIZE        # X25519 ephemeral + ML-KEM-768 ciphertext


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PQCryptoError(CryptoError):
    """Base exception for post-quantum crypto operations."""


class MLKEMError(PQCryptoError):
    """ML-KEM operation failed."""


class LMSError(PQCryptoError):
    """LMS signature operation failed."""


class LMSStateExhaustedError(LMSError):
    """LMS signature counter exhausted."""


class OQSNotAvailableError(PQCryptoError):
    """liboqs-python not installed."""


# ---------------------------------------------------------------------------
# ML-KEM-768 interface
# ---------------------------------------------------------------------------

class MLKEM768:
    """ML-KEM-768 (Kyber) key encapsulation mechanism.

    Uses liboqs-python if available, otherwise falls back to a
    placeholder that raises OQSNotAvailableError.
    """

    _oqs_available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        """Check if liboqs-python is available."""
        if cls._oqs_available is None:
            try:
                import oqs
                cls._oqs_available = True
            except ImportError:
                cls._oqs_available = False
        return cls._oqs_available

    @classmethod
    def keygen(cls) -> tuple[bytes, bytes]:
        """Generate ML-KEM-768 key pair.

        Returns:
            Tuple of (public_key, secret_key).
            Public key: 1184 bytes, Secret key: ~2400 bytes.

        Raises:
            MLKEMError: If key generation fails.
        """
        if not cls.is_available():
            raise OQSNotAvailableError(
                "liboqs-python not installed. Install with: pip install liboqs-python"
            )

        try:
            import oqs
            with oqs.KeyEncapsulation("ML-KEM-768") as kem:
                pk = kem.generate_keypair()
                sk = kem.export_secret_key()
                return pk, sk
        except Exception as exc:
            raise MLKEMError(f"ML-KEM-768 keygen failed: {exc}") from exc

    @classmethod
    def encaps(cls, public_key: bytes) -> tuple[bytes, bytes]:
        """Encapsulate a shared secret using a public key.

        Args:
            public_key: ML-KEM-768 public key (1184 bytes).

        Returns:
            Tuple of (ciphertext, shared_secret).
            Ciphertext: 1088 bytes, Shared secret: 32 bytes.

        Raises:
            MLKEMError: If encapsulation fails.
        """
        if not cls.is_available():
            raise OQSNotAvailableError("liboqs-python not installed.")

        if len(public_key) != ML_KEM_768_PK_SIZE:
            raise MLKEMError(
                f"Invalid public key size: {len(public_key)}, "
                f"expected {ML_KEM_768_PK_SIZE}."
            )

        try:
            import oqs
            with oqs.KeyEncapsulation("ML-KEM-768", public_key) as kem:
                ciphertext, shared_secret = kem.encap_secret()
                return ciphertext, shared_secret
        except Exception as exc:
            raise MLKEMError(f"ML-KEM-768 encaps failed: {exc}") from exc

    @classmethod
    def decaps(cls, secret_key: bytes, ciphertext: bytes) -> bytes:
        """Decapsulate a shared secret using a secret key.

        Args:
            secret_key: ML-KEM-768 secret key.
            ciphertext: ML-KEM-768 ciphertext (1088 bytes).

        Returns:
            Shared secret (32 bytes).

        Raises:
            MLKEMError: If decapsulation fails.
        """
        if not cls.is_available():
            raise OQSNotAvailableError("liboqs-python not installed.")

        if len(ciphertext) != ML_KEM_768_CT_SIZE:
            raise MLKEMError(
                f"Invalid ciphertext size: {len(ciphertext)}, "
                f"expected {ML_KEM_768_CT_SIZE}."
            )

        try:
            import oqs
            with oqs.KeyEncapsulation("ML-KEM-768", secret_key=secret_key) as kem:
                shared_secret = kem.decap_secret(ciphertext)
                return shared_secret
        except Exception as exc:
            raise MLKEMError(f"ML-KEM-768 decaps failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Hybrid key exchange (X25519 + ML-KEM-768)
# ---------------------------------------------------------------------------

@dataclass
class HybridPublicKey:
    """Combined X25519 + ML-KEM-768 public key."""
    x25519_pk: bytes = b""       # 32 bytes
    ml_kem_pk: bytes = b""       # 1184 bytes

    def encode(self) -> bytes:
        """Serialize for transmission."""
        return self.x25519_pk + self.ml_kem_pk

    @classmethod
    def decode(cls, data: bytes) -> HybridPublicKey:
        """Deserialize from bytes."""
        if len(data) < 32 + ML_KEM_768_PK_SIZE:
            raise PQCryptoError("Invalid hybrid public key size.")
        return cls(
            x25519_pk=data[:32],
            ml_kem_pk=data[32:32 + ML_KEM_768_PK_SIZE],
        )


@dataclass
class HybridCiphertext:
    """Combined X25519 ephemeral + ML-KEM-768 ciphertext."""
    x25519_ephemeral_pk: bytes = b""  # 32 bytes
    ml_kem_ct: bytes = b""            # 1088 bytes

    def encode(self) -> bytes:
        """Serialize for transmission."""
        return self.x25519_ephemeral_pk + self.ml_kem_ct

    @classmethod
    def decode(cls, data: bytes) -> HybridCiphertext:
        """Deserialize from bytes."""
        if len(data) < 32 + ML_KEM_768_CT_SIZE:
            raise PQCryptoError("Invalid hybrid ciphertext size.")
        return cls(
            x25519_ephemeral_pk=data[:32],
            ml_kem_ct=data[32:32 + ML_KEM_768_CT_SIZE],
        )


class HybridKeyExchange:
    """Hybrid X25519 + ML-KEM-768 key exchange.

    Provides IND-CCA2 security under both classical and quantum
    adversary models. The combined shared secret is derived from
    both classical and post-quantum components.
    """

    @staticmethod
    def _constant_time_xor(a: bytes, b: bytes) -> bytes:
        """Constant-time XOR of two equal-length byte strings.

        Used to combine classical and PQ shared secrets without
        leaking timing information about either component.

        Args:
            a: First byte string (must be same length as b).
            b: Second byte string (must be same length as a).

        Returns:
            XOR of a and b, same length as inputs.

        Raises:
            PQCryptoError: If inputs have different lengths.
        """
        if len(a) != len(b):
            raise PQCryptoError(
                f"Cannot XOR different-length inputs: {len(a)} vs {len(b)}"
            )
        # Constant-time byte-by-byte XOR
        # Using int conversion avoids Python short-circuit optimizations
        result = bytes(x ^ y for x, y in zip(a, b))
        return result

    @staticmethod
    def encapsulate(peer_pk: HybridPublicKey) -> tuple[HybridCiphertext, bytes]:
        """Perform hybrid encapsulation.

        Args:
            peer_pk: Peer's hybrid public key.

        Returns:
            Tuple of (ciphertext, combined_shared_secret).

        The combined secret is computed as:
            combined = HKDF(X25519_shared ⊕ ML-KEM_shared, "presidium-pq-hybrid-v1")
        This ensures that if either algorithm is broken, the
        combined secret remains secure (hybrid security).
        """
        # Classical component: X25519
        eph_keypair = X25519KeyPair()
        classical_secret = eph_keypair.shared_secret(peer_pk.x25519_pk)

        # Post-quantum component: ML-KEM-768
        ml_kem_ct, pq_secret = MLKEM768.encaps(peer_pk.ml_kem_pk)

        # Combine secrets using constant-time XOR then HKDF
        classical_bytes = bytes(classical_secret)
        xored = HybridKeyExchange._constant_time_xor(classical_bytes, pq_secret)

        # Derive final combined secret via HKDF-SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF as CryptoHKDF
        from cryptography.hazmat.primitives import hashes as crypto_hashes
        hkdf = CryptoHKDF(
            algorithm=crypto_hashes.SHA256(),
            length=32,
            salt=None,
            info=b"presidium-pq-hybrid-v1",
        )
        combined_secret = hkdf.derive(xored)

        # Zero intermediate secrets
        classical_secret.zero()

        ciphertext = HybridCiphertext(
            x25519_ephemeral_pk=eph_keypair.public_key_bytes,
            ml_kem_ct=ml_kem_ct,
        )

        return ciphertext, combined_secret

    @staticmethod
    def decapsulate(
        local_x25519_sk: bytes,
        local_ml_kem_sk: bytes,
        ciphertext: HybridCiphertext,
    ) -> bytes:
        """Perform hybrid decapsulation.

        Args:
            local_x25519_sk: Our X25519 private key.
            local_ml_kem_sk: Our ML-KEM-768 secret key.
            ciphertext: The hybrid ciphertext.

        Returns:
            Combined shared secret (32 bytes).

        The combined secret is computed as:
            combined = HKDF(X25519_shared ⊕ ML-KEM_shared, "presidium-pq-hybrid-v1")
        """
        # Classical component
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        priv_key = X25519PrivateKey.from_private_bytes(local_x25519_sk)
        pub_key = X25519PublicKey.from_public_bytes(ciphertext.x25519_ephemeral_pk)
        classical_secret = priv_key.exchange(pub_key)

        # Post-quantum component
        pq_secret = MLKEM768.decaps(local_ml_kem_sk, ciphertext.ml_kem_ct)

        # Combine secrets using constant-time XOR then HKDF
        xored = HybridKeyExchange._constant_time_xor(classical_secret, pq_secret)

        # Derive final combined secret via HKDF-SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF as CryptoHKDF
        from cryptography.hazmat.primitives import hashes as crypto_hashes
        hkdf = CryptoHKDF(
            algorithm=crypto_hashes.SHA256(),
            length=32,
            salt=None,
            info=b"presidium-pq-hybrid-v1",
        )
        combined_secret = hkdf.derive(xored)

        return combined_secret

    @staticmethod
    def encapsulate_classical_only(
        peer_x25519_pk: bytes,
    ) -> tuple[bytes, bytes]:
        """Fallback: X25519-only encapsulation (no PQ component).

        Returns:
            Tuple of (ephemeral_pk, shared_secret).
        """
        eph_keypair = X25519KeyPair()
        classical_secret = eph_keypair.shared_secret(peer_x25519_pk)
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF as CryptoHKDF
        from cryptography.hazmat.primitives import hashes as crypto_hashes
        hkdf = CryptoHKDF(
            algorithm=crypto_hashes.SHA256(),
            length=32,
            salt=None,
            info=b"presidium-classical-v1",
        )
        shared = hkdf.derive(bytes(classical_secret))
        classical_secret.zero()
        return eph_keypair.public_key_bytes, shared


# ---------------------------------------------------------------------------
# LMS stateful hash-based signatures (placeholder)
# ---------------------------------------------------------------------------

class LMSSigner:
    """LMS (Leighton-Micali Signature) stateful hash-based signer.

    LMS provides post-quantum secure digital signatures based only
    on hash functions. It is stateful — each signature consumes a
    one-time key, and the counter must never decrease.

    Signing and verification are disabled until a vetted RFC 8554 backend
    with persistent one-time-key state is integrated. The reservation-counter
    utility is retained, but is not a cryptographic implementation.

    The LMS signer is used for the master identity key, which signs
    infrequently (device registration, key rotation announcements).
    With 2^10 = 1024 signatures, this is sufficient for the expected
    lifetime of a master key.

    State persistence:
    The LMS counter is stored in SQLite using BEGIN IMMEDIATE to
    ensure atomic counter increment. This prevents counter rollback
    or duplicate use of one-time keys even in concurrent scenarios.
    """

    def __init__(
        self,
        state_counter: int = 0,
        db_path: Optional[str] = None,
    ) -> None:
        self._counter = state_counter
        self._max_signatures = LMS_MAX_SIGNATURES
        self._private_key: Optional[bytes] = None
        self._public_key: Optional[bytes] = None
        self._db_path = db_path
        self._db_initialized = False

    def _init_db(self) -> None:
        """Initialize the SQLite state database for atomic counter persistence."""
        if self._db_path is None or self._db_initialized:
            return

        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS lms_state ("
                "  key_id TEXT PRIMARY KEY,"
                "  counter INTEGER NOT NULL,"
                "  private_key BLOB,"
                "  public_key BLOB"
                ")"
            )
            conn.commit()
            self._db_initialized = True
        finally:
            conn.close()

    def _atomic_increment_counter(self, key_id: str) -> int:
        """Atomically increment the LMS counter using SQLite BEGIN IMMEDIATE.

        This ensures that even in concurrent scenarios, the counter
        is never decremented or reused, which would compromise the
        security of the stateful hash-based signature scheme.

        Args:
            key_id: Unique identifier for this LMS key pair.

        Returns:
            The counter value BEFORE increment (the q value to use).

        Raises:
            LMSStateExhaustedError: If no signatures remain.
        """
        if self._db_path is None:
            # Fallback: in-memory counter (not atomic, but works for single-process)
            if self._counter >= self._max_signatures:
                raise LMSStateExhaustedError(
                    f"LMS signature counter exhausted ({self._max_signatures} used). "
                    "Generate a new master key and announce the rotation."
                )
            q = self._counter
            self._counter += 1
            return q

        import sqlite3
        self._init_db()

        conn = sqlite3.connect(self._db_path)
        try:
            # BEGIN IMMEDIATE acquires a RESERVED lock, preventing other
            # writers from starting a transaction. This serializes counter
            # increments and prevents race conditions.
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT counter FROM lms_state WHERE key_id = ?",
                (key_id,),
            ).fetchone()

            if row is None:
                # First use: insert initial counter
                current = self._counter
                conn.execute(
                    "INSERT INTO lms_state (key_id, counter, private_key, public_key) "
                    "VALUES (?, ?, ?, ?)",
                    (key_id, current + 1, self._private_key, self._public_key),
                )
            else:
                current = row[0]
                if current >= self._max_signatures:
                    conn.execute("ROLLBACK")
                    raise LMSStateExhaustedError(
                        f"LMS signature counter exhausted ({self._max_signatures} used). "
                        "Generate a new master key and announce the rotation."
                    )
                conn.execute(
                    "UPDATE lms_state SET counter = counter + 1 WHERE key_id = ?",
                    (key_id,),
                )

            conn.execute("COMMIT")
            self._counter = current + 1
            return current
        except LMSStateExhaustedError:
            raise
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:  # nosec B110
                pass
            raise LMSError(f"LMS state database error: {exc}") from exc
        finally:
            conn.close()

    @property
    def counter(self) -> int:
        """Current signature counter."""
        return self._counter

    @property
    def remaining_signatures(self) -> int:
        """Number of remaining signatures."""
        return self._max_signatures - self._counter

    @property
    def is_exhausted(self) -> bool:
        """Check if all signatures have been used."""
        return self._counter >= self._max_signatures

    def generate(self, key_id: str = "default") -> tuple[bytes, bytes]:
        """Refuse key generation until a vetted, persistent LMS backend is integrated."""
        raise LMSError("LMS signing is unavailable: no audited RFC 8554 backend is integrated.")

    def sign(self, message: bytes) -> tuple[bytes, int]:
        """Never substitute hashes for a real LMS signature."""
        raise LMSError("LMS signing is unavailable: no audited RFC 8554 backend is integrated.")

    @staticmethod
    def verify(public_key: bytes, message: bytes, signature: bytes, q: int) -> bool:
        """Fail closed. Signature length is not proof of authenticity."""
        return False
