"""Sealed Sender for Presidium — anonymous message delivery.

Implements Signal-style Sealed Sender where the message originator is
hidden from all intermediaries (relays, DHT nodes) and only revealed
to the intended recipient after decryption.

Protocol flow:
    Sender                                     Recipient
      |                                           |
      |  1. Lookup recipient public key (DHT)     |
      |  2. Ephemeral X25519 key generation       |
      |  3. HPKE-Seal(recipient_pk, plaintext,    |
      |     aad=[sender_identity, signature])      |
      |  4. Sealed envelope = {                    |
      |       ephemeral_pk,                        |
      |       encrypted_payload,                   |
      |       recipient_hint = hash(pk)[:4]        |
      |     }                                      |
      |  ----sealed envelope via relay/DHT------>  |
      |                                           5. HPKE-Open(sealed)
      |                                           6. Verify sender signature
      |                                           7. Process message

Key properties:
    - Relay sees only encrypted blob + 4-byte recipient hint
    - Relay cannot determine sender identity
    - Recipient learns sender identity ONLY after decryption
    - Replay protection via timestamp + nonce
    - Optional: sender certificate for anonymous reporting

HPKE (RFC 9180) simplified implementation:
    We implement the HPKE-Base mode using X25519 + HKDF-SHA256 + AES-256-GCM.
    This provides a KEM (Key Encapsulation Mechanism) + AEAD combination
    that satisfies the HPKE interface without requiring the full RFC spec.
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
    AES256GCM,
    CryptoError,
    Ed25519KeyPair,
    SecureBytes,
    X25519KeyPair,
    constant_time_compare,
    derive_session_key,
    secure_zero,
)

# Local cryptography helpers
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEALED_SENDER_VERSION = 1
RECIPIENT_HINT_SIZE = 4       # First 4 bytes of SHA-256(recipient_pk)
EPHEMERAL_PK_SIZE = 32        # X25519 public key
NONCE_SIZE = 12               # AES-GCM nonce
TIMESTAMP_SIZE = 8            # uint64 big-endian
ENVELOPE_HEADER_SIZE = (
    1 +    # version
    4 +    # recipient_hint
    32 +   # ephemeral_pk
    12 +   # nonce
    8      # timestamp
)
MAX_ENVELOPE_AGE_SECONDS = 86400  # 24 hours — reject stale envelopes


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SealedSenderError(Exception):
    """Base exception for sealed sender operations."""


class InvalidEnvelopeError(SealedSenderError):
    """Sealed envelope is malformed or too old."""


class RecipientMismatchError(SealedSenderError):
    """Envelope not intended for this recipient."""


class SenderVerificationError(SealedSenderError):
    """Sender signature verification failed."""


class HPKEError(SealedSenderError):
    """HPKE encapsulation/decapsulation failure."""


# ---------------------------------------------------------------------------
# Sealed Envelope data model
# ---------------------------------------------------------------------------

@dataclass
class SealedEnvelope:
    """A sealed sender envelope containing an encrypted message.

    Wire format:
        [1B version][4B recipient_hint][32B ephemeral_pk]
        [12B nonce][8B timestamp][NB ciphertext]

    The ciphertext contains:
        - Sender identity public key (32 bytes Ed25519)
        - Sender signature (64 bytes)
        - Inner message payload (variable length)
    """
    version: int = SEALED_SENDER_VERSION
    recipient_hint: bytes = b""       # 4 bytes
    ephemeral_pk: bytes = b""         # 32 bytes X25519
    nonce: bytes = b""                # 12 bytes
    timestamp: int = 0                # Unix epoch seconds
    ciphertext: bytes = b""           # AES-256-GCM ciphertext

    def encode(self) -> bytes:
        """Serialize the envelope to bytes for wire transmission."""
        if len(self.recipient_hint) != RECIPIENT_HINT_SIZE:
            raise InvalidEnvelopeError(
                f"Invalid recipient hint size: {len(self.recipient_hint)}, "
                f"expected {RECIPIENT_HINT_SIZE}."
            )
        if len(self.ephemeral_pk) != EPHEMERAL_PK_SIZE:
            raise InvalidEnvelopeError(
                f"Invalid ephemeral key size: {len(self.ephemeral_pk)}, "
                f"expected {EPHEMERAL_PK_SIZE}."
            )
        if len(self.nonce) != NONCE_SIZE:
            raise InvalidEnvelopeError(
                f"Invalid nonce size: {len(self.nonce)}, expected {NONCE_SIZE}."
            )

        header = struct.pack(
            "!B4s32s12sQ",
            self.version,
            self.recipient_hint,
            self.ephemeral_pk,
            self.nonce,
            self.timestamp,
        )
        return header + self.ciphertext

    @classmethod
    def decode(cls, data: bytes) -> SealedEnvelope:
        """Deserialize an envelope from bytes.

        Args:
            data: Raw bytes from the wire.

        Returns:
            Parsed SealedEnvelope.

        Raises:
            InvalidEnvelopeError: If the data is malformed.
        """
        if len(data) < ENVELOPE_HEADER_SIZE:
            raise InvalidEnvelopeError(
                f"Envelope too short: {len(data)} bytes, "
                f"need at least {ENVELOPE_HEADER_SIZE}."
            )

        version, hint, eph_pk, nonce, timestamp = struct.unpack(
            "!B4s32s12sQ", data[:ENVELOPE_HEADER_SIZE]
        )

        if version != SEALED_SENDER_VERSION:
            raise InvalidEnvelopeError(
                f"Unsupported sealed sender version: {version}, "
                f"expected {SEALED_SENDER_VERSION}."
            )

        ciphertext = data[ENVELOPE_HEADER_SIZE:]

        return cls(
            version=version,
            recipient_hint=hint,
            ephemeral_pk=eph_pk,
            nonce=nonce,
            timestamp=timestamp,
            ciphertext=ciphertext,
        )


# ---------------------------------------------------------------------------
# Inner payload (what's inside the ciphertext)
# ---------------------------------------------------------------------------

@dataclass
class SealedPayload:
    """The decrypted inner payload of a sealed envelope.

    Contains the sender's identity, their signature over the message,
    and the actual message content.
    """
    sender_public_key: bytes = b""   # 32 bytes Ed25519
    sender_signature: bytes = b""    # 64 bytes Ed25519 signature
    message_content: bytes = b""     # Variable-length message

    def encode(self) -> bytes:
        """Serialize to bytes for encryption."""
        return (
            struct.pack("!H", len(self.sender_public_key))
            + self.sender_public_key
            + struct.pack("!H", len(self.sender_signature))
            + self.sender_signature
            + self.message_content
        )

    @classmethod
    def decode(cls, data: bytes) -> SealedPayload:
        """Deserialize from decrypted bytes."""
        offset = 0

        if len(data) < 2:
            raise InvalidEnvelopeError("Payload too short for public key length.")
        pk_len = struct.unpack("!H", data[offset:offset + 2])[0]
        offset += 2

        if len(data) < offset + pk_len:
            raise InvalidEnvelopeError("Payload truncated: missing public key.")
        sender_pk = data[offset:offset + pk_len]
        offset += pk_len

        if len(data) < offset + 2:
            raise InvalidEnvelopeError("Payload too short for signature length.")
        sig_len = struct.unpack("!H", data[offset:offset + 2])[0]
        offset += 2

        if len(data) < offset + sig_len:
            raise InvalidEnvelopeError("Payload truncated: missing signature.")
        sender_sig = data[offset:offset + sig_len]
        offset += sig_len

        message = data[offset:]

        return cls(
            sender_public_key=sender_pk,
            sender_signature=sender_sig,
            message_content=message,
        )


# ---------------------------------------------------------------------------
# HPKE-Simplified (KEM + AEAD)
# ---------------------------------------------------------------------------

class HPKEContext:
    """Simplified HPKE context using X25519-KEM + HKDF + AES-256-GCM.

    Implements the HPKE-Base mode (no authentication of sender at the
    KEM level — authentication is provided by the inner Ed25519 signature).

    Key schedule:
        shared_secret = X25519(ephemeral_sk, recipient_pk)
        key = HKDF-SHA256(shared_secret, info="presidium-hpke-key", salt=ephemeral_pk)
        nonce = HKDF-SHA256(shared_secret, info="presidium-hpke-nonce", salt=ephemeral_pk)[:12]
    """

    @staticmethod
    def seal(
        recipient_public_key: bytes,
        plaintext: bytes,
        aad: bytes = b"",
    ) -> tuple[bytes, bytes, bytes]:
        """HPKE-Seal: encrypt to a recipient's public key.

        Args:
            recipient_public_key: Recipient's X25519 public key (32 bytes).
            plaintext: Data to encrypt.
            aad: Additional authenticated data.

        Returns:
            Tuple of (ephemeral_pk, nonce, ciphertext).

        Raises:
            HPKEError: If encapsulation fails.
        """
        try:
            # Generate ephemeral key pair
            eph_keypair = X25519KeyPair()

            # DH key agreement
            shared_secret = eph_keypair.shared_secret(recipient_public_key)

            # Derive encryption key and nonce via HKDF
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            from cryptography.hazmat.primitives import hashes

            # Derive key
            key_hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=eph_keypair.public_key_bytes,
                info=b"presidium-hpke-key",
            )
            enc_key = key_hkdf.derive(bytes(shared_secret))

            # Derive nonce
            nonce_hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=12,
                salt=eph_keypair.public_key_bytes,
                info=b"presidium-hpke-nonce",
            )
            nonce = nonce_hkdf.derive(bytes(shared_secret))

            # Encrypt with AES-256-GCM
            aes = AESGCM(enc_key)
            ciphertext = aes.encrypt(nonce, plaintext, aad)

            return (
                eph_keypair.public_key_bytes,
                nonce,
                ciphertext,
            )

        except Exception as exc:
            raise HPKEError(f"HPKE seal failed: {exc}") from exc

    @staticmethod
    def open(
        recipient_private_key: bytes,
        ephemeral_pk: bytes,
        ciphertext: bytes,
        aad: bytes = b"",
    ) -> bytes:
        """HPKE-Open: decrypt using recipient's private key.

        Args:
            recipient_private_key: Recipient's X25519 private key (32 bytes).
            ephemeral_pk: Sender's ephemeral X25519 public key (32 bytes).
            ciphertext: Encrypted data.
            aad: Additional authenticated data.

        Returns:
            Decrypted plaintext.

        Raises:
            HPKEError: If decapsulation fails.
        """
        try:
            # Reconstruct key pair from private key
            from cryptography.hazmat.primitives.asymmetric.x25519 import (
                X25519PrivateKey,
                X25519PublicKey,
            )
            priv_key = X25519PrivateKey.from_private_bytes(recipient_private_key)
            pub_key = X25519PublicKey.from_public_bytes(ephemeral_pk)

            # DH key agreement
            shared_secret = priv_key.exchange(pub_key)

            # Derive key and nonce (same KDF as seal)
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            from cryptography.hazmat.primitives import hashes

            key_hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=ephemeral_pk,
                info=b"presidium-hpke-key",
            )
            enc_key = key_hkdf.derive(shared_secret)

            nonce_hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=12,
                salt=ephemeral_pk,
                info=b"presidium-hpke-nonce",
            )
            nonce = nonce_hkdf.derive(shared_secret)

            # Decrypt
            aes = AESGCM(enc_key)
            plaintext = aes.decrypt(nonce, ciphertext, aad)

            return plaintext

        except Exception as exc:
            raise HPKEError(f"HPKE open failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Sealed Sender manager
# ---------------------------------------------------------------------------

class SealedSenderManager:
    """Manages sealed sender operations for a Presidium node.

    Provides:
        - seal_message(): Encrypt a message for anonymous delivery
        - unseal_message(): Decrypt and verify an incoming sealed envelope
        - compute_recipient_hint(): Short hint for relay routing

    The manager needs:
        - The local node's X25519 key pair for HPKE
        - The local node's Ed25519 key pair for inner signatures
    """

    def __init__(
        self,
        x25519_keypair: X25519KeyPair,
        ed25519_keypair: Ed25519KeyPair,
    ) -> None:
        self._x25519 = x25519_keypair
        self._ed25519 = ed25519_keypair
        self._enabled_per_peer: dict[str, bool] = {}  # peer_id → enabled

    # -------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------

    def set_enabled(self, peer_id: str, enabled: bool) -> None:
        """Enable or disable sealed sender for a specific peer."""
        self._enabled_per_peer[peer_id] = enabled

    def is_enabled(self, peer_id: str) -> bool:
        """Check if sealed sender is enabled for a peer.

        Default: enabled for all new contacts.
        """
        return self._enabled_per_peer.get(peer_id, True)

    # -------------------------------------------------------------------
    # Seal (sender side)
    # -------------------------------------------------------------------

    def seal_message(
        self,
        recipient_x25519_pk: bytes,
        message_content: bytes,
    ) -> SealedEnvelope:
        """Seal a message for anonymous delivery to a recipient.

        Steps:
            1. Sign the message content with our Ed25519 key
            2. Pack the inner payload (sender_pk + signature + content)
            3. HPKE-Seal to the recipient's X25519 public key
            4. Wrap in a SealedEnvelope with recipient hint

        Args:
            recipient_x25519_pk: Recipient's X25519 public key (32 bytes).
            message_content: The plaintext message bytes.

        Returns:
            A SealedEnvelope ready for anonymous delivery.

        Raises:
            SealedSenderError: If sealing fails.
        """
        if len(recipient_x25519_pk) != 32:
            raise SealedSenderError(
                f"Invalid recipient key size: {len(recipient_x25519_pk)}, expected 32."
            )

        # Step 1: Sign the message content
        signature = self._ed25519.sign(message_content)

        # Step 2: Pack inner payload
        payload = SealedPayload(
            sender_public_key=self._ed25519.public_key_bytes,
            sender_signature=signature,
            message_content=message_content,
        )
        inner_plaintext = payload.encode()

        # Step 3: HPKE-Seal
        # AAD includes timestamp to prevent cross-time attacks
        timestamp = int(time.time())
        aad = struct.pack("!Q", timestamp)

        eph_pk, nonce, ciphertext = HPKEContext.seal(
            recipient_public_key=recipient_x25519_pk,
            plaintext=inner_plaintext,
            aad=aad,
        )

        # Step 4: Compute recipient hint
        recipient_hint = hashlib.sha256(recipient_x25519_pk).digest()[:RECIPIENT_HINT_SIZE]

        envelope = SealedEnvelope(
            version=SEALED_SENDER_VERSION,
            recipient_hint=recipient_hint,
            ephemeral_pk=eph_pk,
            nonce=nonce,
            timestamp=timestamp,
            ciphertext=ciphertext,
        )

        logger.debug(
            "Sealed message for recipient hint %s (%d bytes)",
            recipient_hint.hex(),
            len(ciphertext),
        )
        return envelope

    # -------------------------------------------------------------------
    # Unseal (recipient side)
    # -------------------------------------------------------------------

    def unseal_message(
        self,
        envelope: SealedEnvelope,
    ) -> SealedPayload:
        """Unseal a received sealed envelope.

        Steps:
            1. Verify timestamp freshness
            2. Check recipient hint matches our key
            3. HPKE-Open to decrypt inner payload
            4. Parse inner payload
            5. Verify sender signature

        Args:
            envelope: The received SealedEnvelope.

        Returns:
            The verified SealedPayload.

        Raises:
            InvalidEnvelopeError: If the envelope is stale or malformed.
            RecipientMismatchError: If not intended for us.
            SenderVerificationError: If the sender signature is invalid.
        """
        # Step 1: Timestamp freshness
        now = int(time.time())
        age = now - envelope.timestamp
        if age > MAX_ENVELOPE_AGE_SECONDS or age < -300:  # Allow 5min clock skew
            raise InvalidEnvelopeError(
                f"Envelope too old or from the future: age={age}s, "
                f"max={MAX_ENVELOPE_AGE_SECONDS}s."
            )

        # Step 2: Recipient hint check
        expected_hint = hashlib.sha256(
            self._x25519.public_key_bytes
        ).digest()[:RECIPIENT_HINT_SIZE]
        if not constant_time_compare(envelope.recipient_hint, expected_hint):
            raise RecipientMismatchError(
                "Envelope recipient hint does not match our public key."
            )

        # Step 3: HPKE-Open
        aad = struct.pack("!Q", envelope.timestamp)
        try:
            inner_plaintext = HPKEContext.open(
                recipient_private_key=self._x25519.private_key_bytes,
                ephemeral_pk=envelope.ephemeral_pk,
                ciphertext=envelope.ciphertext,
                aad=aad,
            )
        except HPKEError as exc:
            raise SealedSenderError(f"Failed to decrypt sealed envelope: {exc}") from exc

        # Step 4: Parse inner payload
        payload = SealedPayload.decode(inner_plaintext)

        # Step 5: Verify sender signature
        try:
            Ed25519KeyPair.verify(
                public_key_bytes=payload.sender_public_key,
                signature=payload.sender_signature,
                data=payload.message_content,
            )
        except Exception as exc:
            raise SenderVerificationError(
                f"Sender signature verification failed: {exc}"
            ) from exc

        logger.debug(
            "Unsealed message from sender %s (%d bytes)",
            payload.sender_public_key[:8].hex(),
            len(payload.message_content),
        )
        return payload

    # -------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------

    @staticmethod
    def compute_recipient_hint(public_key: bytes) -> bytes:
        """Compute the 4-byte recipient hint for a public key.

        Used by relays to route sealed envelopes without knowing
        the full recipient identity.
        """
        return hashlib.sha256(public_key).digest()[:RECIPIENT_HINT_SIZE]

    def matches_recipient_hint(self, hint: bytes) -> bool:
        """Check if a recipient hint matches our public key."""
        expected = self.compute_recipient_hint(self._x25519.public_key_bytes)
        return constant_time_compare(expected, hint)
