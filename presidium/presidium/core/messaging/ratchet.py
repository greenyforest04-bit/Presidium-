"""Full Double Ratchet implementation for Presidium.

Implements the Signal Protocol Double Ratchet algorithm:
- Root key evolution via DH ratchet steps (X25519)
- Chain key advancement via HKDF for symmetric ratchet
- Message key derivation per message (forward secrecy)
- Header encryption via header key
- Out-of-order message support with skipped message key cache (max 1000)
- Constant-time comparison for MAC verification

Reference: https://signal.org/docs/specifications/doubleratchet/

State layout:
    Root key (32 bytes) — evolves with each DH ratchet step
    Sending chain key (32 bytes) — advances per sent message
    Receiving chain key (32 bytes) — advances per received message
    DH ratchet key pair (X25519) — our current sending ephemeral
    DH ratchet remote public (32 bytes) — peer's last sending ephemeral
    Header keys (send/recv) — derived alongside chain keys
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from presidium.core.crypto import (
    AES256GCM,
    SecureBytes,
    X25519KeyPair,
    CryptoError,
    EncryptionError,
    DecryptionError,
    constant_time_compare,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class RatchetError(Exception):
    """Base exception for Double Ratchet operations."""


class RatchetNotInitializedError(RatchetError):
    """Attempted operation before ratchet initialization."""


class SkippedMessageKeyNotFoundError(RatchetError):
    """No message key found for an out-of-order message."""


class TooManySkippedMessagesError(RatchetError):
    """Too many skipped message keys — possible replay or DoS."""


class HeaderDecryptError(RatchetError):
    """Failed to decrypt message header."""


class MessageAuthenticationError(RatchetError):
    """Message authentication failed (GCM tag mismatch)."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT_KEY_INFO = b"PresidiumRootKey"
CHAIN_KEY_INFO = b"PresidiumChainKey"
MESSAGE_KEY_INFO = b"PresidiumMessageKey"
HEADER_KEY_INFO = b"PresidiumHeaderKey"
NEXT_HEADER_KEY_INFO = b"PresidiumNextHeaderKey"

MAX_SKIP = 1000  # Maximum number of skipped message keys to cache
KEY_LENGTH = 32  # All keys are 256-bit
NONCE_SIZE = 12  # GCM nonce size


# ---------------------------------------------------------------------------
# HKDF helper
# ---------------------------------------------------------------------------

def _hkdf_expand(key: bytes, info: bytes, length: int = KEY_LENGTH) -> bytes:
    """Single-step HKDF-expand (assumes key is already a valid PRK)."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info,
    )
    return hkdf.derive(key)


def _hkdf_expand_pair(key: bytes, info1: bytes, info2: bytes) -> tuple[bytes, bytes]:
    """Derive two 32-byte keys from one input key using HKDF with different info."""
    # Derive 64 bytes and split
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=None,
        info=info1 + info2,
    )
    combined = hkdf.derive(key)
    return combined[:32], combined[32:]


# ---------------------------------------------------------------------------
# Chain key advancement
# ---------------------------------------------------------------------------

def advance_chain_key(chain_key: bytes) -> tuple[bytes, bytes]:
    """Advance a chain key and derive a message key.

    Returns:
        (new_chain_key, message_key) — both 32 bytes.
    """
    message_key = _hkdf_expand(chain_key, MESSAGE_KEY_INFO)
    new_chain_key = _hkdf_expand(chain_key, CHAIN_KEY_INFO)
    return new_chain_key, message_key


def derive_header_keys(chain_key: bytes) -> tuple[bytes, bytes]:
    """Derive header key and next header key from chain key.

    Returns:
        (header_key, next_header_key) — both 32 bytes.
    """
    hk = _hkdf_expand(chain_key, HEADER_KEY_INFO)
    nhk = _hkdf_expand(chain_key, NEXT_HEADER_KEY_INFO)
    return hk, nhk


# ---------------------------------------------------------------------------
# Encrypted header
# ---------------------------------------------------------------------------

@dataclass
class RatchetHeader:
    """Unencrypted ratchet header attached to each message.

    Contains the sender's current DH ratchet public key and the
    message number in the current sending chain.
    """
    dh_public_key: bytes  # 32 bytes X25519 public key
    message_number: int   # sequence number in current chain
    previous_chain_length: int  # length of previous sending chain


def encode_header(header: RatchetHeader) -> bytes:
    """Encode a RatchetHeader to bytes for encryption."""
    return (
        header.dh_public_key
        + struct.pack("!II", header.message_number, header.previous_chain_length)
    )


def decode_header(data: bytes) -> RatchetHeader:
    """Decode a RatchetHeader from bytes."""
    if len(data) < 32 + 8:
        raise RatchetError(f"Header too short: {len(data)} bytes, need at least 40.")
    dh_public_key = data[:32]
    msg_num, prev_chain_len = struct.unpack("!II", data[32:40])
    return RatchetHeader(
        dh_public_key=dh_public_key,
        message_number=msg_num,
        previous_chain_length=prev_chain_len,
    )


def encrypt_header(header: RatchetHeader, header_key: bytes) -> bytes:
    """Encrypt a ratchet header using AES-256-GCM with the header key.

    Returns:
        nonce + ciphertext (header is small, so this is compact).
    """
    plaintext = encode_header(header)
    return AES256GCM.encrypt(header_key, plaintext)


def decrypt_header(encrypted: bytes, header_key: bytes) -> RatchetHeader:
    """Decrypt a ratchet header.

    Raises:
        HeaderDecryptError: If the header cannot be decrypted.
    """
    try:
        plaintext = AES256GCM.decrypt(header_key, encrypted)
        return decode_header(plaintext)
    except DecryptionError as exc:
        raise HeaderDecryptError(f"Header decryption failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skipped message key store
# ---------------------------------------------------------------------------

class SkippedMessageKeys:
    """Bounded cache of skipped message keys for out-of-order support.

    Keys are stored as (header_key, message_number) → message_key.
    The cache is bounded to MAX_SKIP entries to prevent unbounded memory
    growth from a malicious peer sending many skipped messages.
    """

    def __init__(self, max_skip: int = MAX_SKIP) -> None:
        self._max_skip = max_skip
        # OrderedDict for LRU-like eviction
        self._store: OrderedDict[tuple[bytes, int], bytes] = OrderedDict()

    def store(self, header_key: bytes, message_number: int, message_key: bytes) -> None:
        """Store a skipped message key."""
        if len(self._store) >= self._max_skip:
            # Evict oldest entry
            self._store.popitem(last=False)
        self._store[(header_key, message_number)] = message_key

    def retrieve(self, header_key: bytes, message_number: int) -> Optional[bytes]:
        """Retrieve a skipped message key, or None if not found."""
        key = (header_key, message_number)
        msg_key = self._store.get(key)
        if msg_key is not None:
            del self._store[key]
        return msg_key

    def has_key(self, header_key: bytes, message_number: int) -> bool:
        """Check if a skipped message key exists."""
        return (header_key, message_number) in self._store

    @property
    def count(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        """Clear all stored skipped keys."""
        for key in list(self._store.values()):
            # Best-effort zeroing
            try:
                from presidium.core.crypto import secure_zero
                secure_zero(bytearray(key))
            except Exception:  # nosec B110
                pass
        self._store.clear()


# ---------------------------------------------------------------------------
# Double Ratchet State
# ---------------------------------------------------------------------------

@dataclass
class RatchetState:
    """Complete state of a Double Ratchet session.

    This is the internal state that must be persisted for the session
    to survive across application restarts. All keys are 32 bytes.
    """
    # Root key — evolves with each DH ratchet step
    root_key: bytes = b""

    # Sending chain
    send_chain_key: bytes = b""
    send_header_key: bytes = b""
    send_next_header_key: bytes = b""
    send_message_number: int = 0

    # Receiving chain
    recv_chain_key: bytes = b""
    recv_header_key: bytes = b""
    recv_next_header_key: bytes = b""
    recv_message_number: int = 0

    # Previous sending chain length (for skip computation)
    previous_send_chain_length: int = 0

    # DH ratchet keys
    dh_local_private: bytes = b""  # serialized X25519 private key
    dh_local_public: bytes = b""   # 32 bytes
    dh_remote_public: bytes = b""  # 32 bytes

    # Whether we've performed at least one DH ratchet step as receiver
    initialized: bool = False

    # Skipped message keys
    skipped_keys: SkippedMessageKeys = field(default_factory=SkippedMessageKeys)


# ---------------------------------------------------------------------------
# Double Ratchet
# ---------------------------------------------------------------------------

class DoubleRatchet:
    """Full Signal Protocol Double Ratchet implementation.

    Usage:
        # Alice initiates
        alice = DoubleRatchet()
        alice.init_as_initiator(shared_secret, bob_prekey)

        # Bob responds
        bob = DoubleRatchet()
        bob.init_as_responder(shared_secret, alice_prekey)

        # Alice encrypts
        header, ciphertext = alice.encrypt(plaintext)

        # Bob decrypts
        plaintext = bob.decrypt(header, ciphertext)

    The ratchet provides:
    - Forward secrecy: each message uses a unique key derived from a chain
    - Break-in recovery: a DH ratchet step occurs when the peer's DH key changes
    - Out-of-order support: skipped message keys are cached (bounded)
    """

    def __init__(self) -> None:
        self._state = RatchetState()

    # -------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------

    def init_as_initiator(
        self,
        shared_secret: bytes,
        remote_dh_public: bytes,
    ) -> bytes:
        """Initialize as the initiator (Alice).

        Follows the Signal Double Ratchet specification:
        1. Set root_key = X3DH shared_secret
        2. Generate new DH ratchet key pair
        3. Perform DH with the responder's prekey
        4. KDF_RK(root_key, dh_output) → (sending_chain_key, new_root_key)

        Args:
            shared_secret: The X3DH shared secret from initial key exchange.
            remote_dh_public: The peer's prekey (X25519 public, 32 bytes).

        Returns:
            Our initial DH public key to send to the peer.
        """
        # Generate our initial DH ratchet key pair
        dh_keypair = X25519KeyPair()
        self._state.dh_local_private = dh_keypair.private_key_bytes
        self._state.dh_local_public = dh_keypair.public_key_bytes
        self._state.dh_remote_public = remote_dh_public

        # Perform DH ratchet step: derive sending chain from shared_secret + DH output
        dh_output = dh_keypair.shared_secret(remote_dh_public)
        dh_bytes = bytes(dh_output)

        # KDF_RK(shared_secret, dh_output) → (sending_chain, new_root_key)
        # Per Signal spec: root_key starts as X3DH shared_secret
        send_chain, new_root = self._kdf_rk(shared_secret, dh_bytes)
        self._state.root_key = new_root
        self._state.send_chain_key = send_chain
        self._state.send_header_key = _hkdf_expand(send_chain, HEADER_KEY_INFO)
        self._state.send_next_header_key = _hkdf_expand(send_chain, NEXT_HEADER_KEY_INFO)

        # Receiving chain is not yet established (until peer sends a message)
        self._state.recv_chain_key = b""
        self._state.recv_header_key = b""
        self._state.recv_next_header_key = _hkdf_expand(b"", NEXT_HEADER_KEY_INFO)

        self._state.send_message_number = 0
        self._state.recv_message_number = 0
        self._state.previous_send_chain_length = 0
        self._state.initialized = True

        # Zero DH output
        dh_output.zero()

        return self._state.dh_local_public

    def init_as_responder(
        self,
        shared_secret: bytes,
        remote_dh_public: bytes,
        local_prekey_private: Optional[bytes] = None,
    ) -> None:
        """Initialize as the responder (Bob).

        Follows the Signal Double Ratchet specification:
        1. Set root_key = X3DH shared_secret
        2. Compute DH with the initiator's first DH ratchet key using our prekey
        3. KDF_RK(root_key, dh_output) → (receiving_chain_key, new_root_key)

        Args:
            shared_secret: The X3DH shared secret from initial key exchange.
            remote_dh_public: The initiator's first DH ratchet public key (32 bytes).
            local_prekey_private: Our prekey private key (32 bytes) that the
                initiator used. This is needed to compute the same DH output
                as the initiator. If None, the receiving chain cannot be
                established until a DH ratchet step occurs during decrypt.
        """
        self._state.dh_remote_public = remote_dh_public
        self._state.root_key = shared_secret

        if local_prekey_private is not None:
            # Compute the same DH output as the initiator:
            # DH(our_prekey_priv, their_first_dh_pub) = DH(their_first_dh_priv, our_prekey_pub)
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
            prekey = X25519PrivateKey.from_private_bytes(local_prekey_private)
            remote_pk = X25519PublicKey.from_public_bytes(remote_dh_public)
            dh_output = prekey.exchange(remote_pk)

            # KDF_RK(shared_secret, dh_output) → (receiving_chain, new_root_key)
            # This MUST produce the same chain key as the initiator's sending chain
            recv_chain, new_root = self._kdf_rk(shared_secret, dh_output)
            self._state.root_key = new_root

            # Derive header keys from receiving chain
            self._state.recv_header_key = _hkdf_expand(recv_chain, HEADER_KEY_INFO)
            self._state.recv_next_header_key = _hkdf_expand(recv_chain, NEXT_HEADER_KEY_INFO)
            self._state.recv_chain_key = recv_chain
        else:
            # Without the prekey private key, we cannot derive the receiving chain
            # yet. The first decrypt will handle this via the DH ratchet step.
            # The recv_next_header_key will be tried during decryption.
            recv_chain, new_root = self._kdf_rk(shared_secret, b"")
            self._state.root_key = new_root
            self._state.recv_next_header_key = _hkdf_expand(recv_chain, NEXT_HEADER_KEY_INFO)
            self._state.recv_header_key = _hkdf_expand(recv_chain, HEADER_KEY_INFO)
            self._state.recv_chain_key = recv_chain

        # Sending chain is not yet established (until we perform a DH step)
        self._state.send_chain_key = b""
        self._state.send_header_key = b""
        self._state.send_next_header_key = _hkdf_expand(b"", NEXT_HEADER_KEY_INFO)

        # Generate but don't use our DH key yet (used when we first send)
        dh_keypair = X25519KeyPair()
        self._state.dh_local_private = dh_keypair.private_key_bytes
        self._state.dh_local_public = dh_keypair.public_key_bytes

        self._state.send_message_number = 0
        self._state.recv_message_number = 0
        self._state.previous_send_chain_length = 0
        self._state.initialized = True

    # -------------------------------------------------------------------
    # Encryption
    # -------------------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> tuple[bytes, bytes]:
        """Encrypt a message using the Double Ratchet.

        Advances the sending chain key to derive a per-message key,
        then encrypts with AES-256-GCM.

        If the sending chain hasn't been established yet (responder's first
        message), a DH ratchet step is performed first to create it.

        Args:
            plaintext: The message content to encrypt.

        Returns:
            Tuple of (encrypted_header, encrypted_message).
            - encrypted_header: nonce + AES-GCM(header_key, header)
            - encrypted_message: nonce + AES-GCM(message_key, plaintext, aad=header)

        Raises:
            RatchetNotInitializedError: If the ratchet hasn't been initialized.
        """
        if not self._state.initialized:
            raise RatchetNotInitializedError("Ratchet not initialized.")

        # If sending chain is not established, perform a DH ratchet step
        # This happens when the responder sends their first message
        if not self._state.send_chain_key or not self._state.send_header_key:
            self._perform_sending_dh_ratchet()

        # Advance sending chain
        new_chain_key, message_key = advance_chain_key(self._state.send_chain_key)
        self._state.send_chain_key = new_chain_key

        # Build header
        header = RatchetHeader(
            dh_public_key=self._state.dh_local_public,
            message_number=self._state.send_message_number,
            previous_chain_length=self._state.previous_send_chain_length,
        )

        # Encrypt header with header key
        encrypted_header = encrypt_header(header, self._state.send_header_key)

        # Encrypt message with message key, using the raw header as AAD
        raw_header = encode_header(header)
        encrypted_message = AES256GCM.encrypt(message_key, plaintext, aad=raw_header)

        self._state.send_message_number += 1

        # Zero message key from memory
        _zero_bytes(message_key)

        return encrypted_header, encrypted_message

    # -------------------------------------------------------------------
    # Decryption
    # -------------------------------------------------------------------

    def decrypt(self, encrypted_header: bytes, encrypted_message: bytes) -> bytes:
        """Decrypt a message using the Double Ratchet.

        Handles:
        - Header decryption with current or next header key
        - DH ratchet step when peer's DH key changes
        - Skipped message key caching for out-of-order messages
        - Chain advancement for in-order messages
        - Retrieval from skipped keys cache for late-arriving messages

        Args:
            encrypted_header: The encrypted ratchet header.
            encrypted_message: The encrypted message body.

        Returns:
            The decrypted plaintext.

        Raises:
            RatchetNotInitializedError: If not initialized.
            HeaderDecryptError: If header cannot be decrypted.
            MessageAuthenticationError: If message authentication fails.
            TooManySkippedMessagesError: If too many messages are skipped.
        """
        if not self._state.initialized:
            raise RatchetNotInitializedError("Ratchet not initialized.")

        # Try to decrypt header with current and next header keys
        header: Optional[RatchetHeader] = None
        used_header_key: bytes = b""

        # Try current recv header key
        if self._state.recv_header_key:
            try:
                header = decrypt_header(encrypted_header, self._state.recv_header_key)
                used_header_key = self._state.recv_header_key
            except HeaderDecryptError:
                header = None

        # Try next recv header key (DH ratchet step needed)
        if header is None and self._state.recv_next_header_key:
            try:
                header = decrypt_header(encrypted_header, self._state.recv_next_header_key)
                used_header_key = self._state.recv_next_header_key
            except HeaderDecryptError:
                header = None

        if header is None:
            raise HeaderDecryptError("Cannot decrypt header with any known header key.")

        # Check if this is a late-arriving message (already in skipped keys cache)
        # This handles out-of-order messages that arrive after we've already
        # advanced the receiving chain past their message number.
        if header.message_number < self._state.recv_message_number:
            cached_key = self._state.skipped_keys.retrieve(
                used_header_key, header.message_number
            )
            if cached_key is not None:
                # Found in cache — decrypt with cached key
                raw_header = encode_header(header)
                try:
                    plaintext = AES256GCM.decrypt(
                        cached_key, encrypted_message, aad=raw_header
                    )
                except DecryptionError as exc:
                    raise MessageAuthenticationError(
                        f"Message authentication failed for cached key: {exc}"
                    ) from exc
                _zero_bytes(cached_key)
                return plaintext
            # Not in cache — might be a replay or error
            # Fall through to normal processing (will likely fail)

        # Check if DH ratchet step is needed (peer's DH key changed)
        # Use constant-time comparison to prevent timing side-channel
        # on public key changes
        if not constant_time_compare(header.dh_public_key, self._state.dh_remote_public):
            self._skip_message_keys(header.previous_chain_length)
            self._dh_ratchet_step(header)

        # Skip message keys if we're behind
        self._skip_message_keys(header.message_number)

        # Advance receiving chain to derive message key
        new_chain_key, message_key = advance_chain_key(self._state.recv_chain_key)
        self._state.recv_chain_key = new_chain_key
        self._state.recv_message_number = header.message_number + 1

        # Decrypt message with AAD = raw header
        raw_header = encode_header(header)
        try:
            plaintext = AES256GCM.decrypt(message_key, encrypted_message, aad=raw_header)
        except DecryptionError as exc:
            # Store the skipped key in case we need it later
            self._state.skipped_keys.store(used_header_key, header.message_number, message_key)
            raise MessageAuthenticationError(f"Message authentication failed: {exc}") from exc

        # Zero message key
        _zero_bytes(message_key)

        return plaintext

    # -------------------------------------------------------------------
    # DH Ratchet Step
    # -------------------------------------------------------------------

    def _dh_ratchet_step(self, header: RatchetHeader) -> None:
        """Perform a DH ratchet step.

        Called when the peer's DH public key changes (they've ratcheted).
        This:
        1. Updates our receiving chain with the peer's new DH key
        2. Generates a new DH key pair for our sending chain
        3. Derives new root key, chain keys, and header keys
        """
        # Save previous send chain length
        self._state.previous_send_chain_length = self._state.send_message_number

        # Update remote DH public key
        self._state.dh_remote_public = header.dh_public_key

        # DH output with peer's new key (using our current private key)
        current_dh = X25519KeyPair(
            X25519PrivateKey.from_private_bytes(self._state.dh_local_private)
        )
        dh_output = current_dh.shared_secret(self._state.dh_remote_public)
        dh_bytes = bytes(dh_output)

        # Derive new receiving chain
        recv_chain, new_root = self._kdf_rk(self._state.root_key, dh_bytes)
        self._state.root_key = new_root
        self._state.recv_chain_key = recv_chain
        self._state.recv_header_key = self._state.recv_next_header_key
        self._state.recv_next_header_key = _hkdf_expand(recv_chain, NEXT_HEADER_KEY_INFO)

        # Generate new DH key pair for sending
        new_dh = X25519KeyPair()
        self._state.dh_local_private = new_dh.private_key_bytes
        self._state.dh_local_public = new_dh.public_key_bytes

        # DH output with new key pair
        dh_output2 = new_dh.shared_secret(self._state.dh_remote_public)
        dh_bytes2 = bytes(dh_output2)

        # Derive new sending chain
        send_chain, new_root2 = self._kdf_rk(new_root, dh_bytes2)
        self._state.root_key = new_root2
        self._state.send_chain_key = send_chain
        self._state.send_header_key = self._state.send_next_header_key
        self._state.send_next_header_key = _hkdf_expand(send_chain, NEXT_HEADER_KEY_INFO)

        self._state.send_message_number = 0
        self._state.recv_message_number = 0

        # Zero DH outputs
        dh_output.zero()
        dh_output2.zero()

    def _perform_sending_dh_ratchet(self) -> None:
        """Perform a sending DH ratchet step.

        This is called when the sending chain hasn't been established yet,
        which happens when the responder sends their first message. It:
        1. Saves previous send chain length
        2. Generates a new DH key pair for sending
        3. Derives sending chain from root key + DH output
        """
        # Save previous send chain length
        self._state.previous_send_chain_length = self._state.send_message_number

        # Generate new DH key pair for sending
        new_dh = X25519KeyPair()
        self._state.dh_local_private = new_dh.private_key_bytes
        self._state.dh_local_public = new_dh.public_key_bytes

        # DH output with peer's key using new key pair
        if self._state.dh_remote_public:
            dh_output = new_dh.shared_secret(self._state.dh_remote_public)
            dh_bytes = bytes(dh_output)

            # Derive new sending chain
            send_chain, new_root = self._kdf_rk(self._state.root_key, dh_bytes)
            self._state.root_key = new_root
            self._state.send_chain_key = send_chain
            self._state.send_header_key = self._state.send_next_header_key
            self._state.send_next_header_key = _hkdf_expand(send_chain, NEXT_HEADER_KEY_INFO)

            dh_output.zero()
        else:
            # No remote key yet - this shouldn't happen in normal operation
            raise RatchetError("Cannot perform sending DH ratchet: no remote public key")

        self._state.send_message_number = 0

    # -------------------------------------------------------------------
    # Skip message keys
    # -------------------------------------------------------------------

    def _skip_message_keys(self, until: int) -> None:
        """Skip message keys in the receiving chain until the given number.

        Stores skipped keys in the cache for potential later use.
        Raises TooManySkippedMessagesError if the skip count exceeds MAX_SKIP,
        and emits a CRITICAL alert to the security log.
        """
        skip_count = until - self._state.recv_message_number
        if skip_count > MAX_SKIP:
            # SECURITY ALERT: This could indicate a replay attack, DoS,
            # or a malfunctioning peer. Log at CRITICAL level for monitoring.
            logger.critical(
                "SECURITY ALERT: Skipped message key window exceeded! "
                "skip_count=%d, max_skip=%d, recv_msg_num=%d, until=%d. "
                "Possible replay attack or DoS.",
                skip_count, MAX_SKIP, self._state.recv_message_number, until,
            )
            raise TooManySkippedMessagesError(
                f"Cannot skip {skip_count} messages "
                f"(max {MAX_SKIP}). Possible replay attack or DoS."
            )

        # Warn when approaching the limit (>= 80% of MAX_SKIP)
        if skip_count >= int(MAX_SKIP * 0.8):
            logger.warning(
                "Skipped message key window approaching limit: "
                "skip_count=%d/%d (%.0f%%). Monitor for suspicious activity.",
                skip_count, MAX_SKIP, (skip_count / MAX_SKIP) * 100,
            )

        while self._state.recv_message_number < until:
            new_chain_key, message_key = advance_chain_key(self._state.recv_chain_key)
            self._state.skipped_keys.store(
                self._state.recv_header_key,
                self._state.recv_message_number,
                message_key,
            )
            self._state.recv_chain_key = new_chain_key
            self._state.recv_message_number += 1

    # -------------------------------------------------------------------
    # Root key KDF
    # -------------------------------------------------------------------

    def _kdf_rk(self, root_key: bytes, dh_output: bytes) -> tuple[bytes, bytes]:
        """KDF for root key evolution.

        Args:
            root_key: Current root key (32 bytes).
            dh_output: DH ratchet output (32 bytes), may be empty for initial setup.

        Returns:
            (chain_key, new_root_key) — both 32 bytes.
        """
        if dh_output:
            input_key = root_key + dh_output
        else:
            input_key = root_key

        combined = _hkdf_expand(input_key, ROOT_KEY_INFO + CHAIN_KEY_INFO, 64)
        chain_key = combined[:32]
        new_root_key = combined[32:]
        return chain_key, new_root_key

    # -------------------------------------------------------------------
    # State access
    # -------------------------------------------------------------------

    @property
    def state(self) -> RatchetState:
        """Get the current ratchet state (for persistence)."""
        return self._state

    @state.setter
    def state(self, new_state: RatchetState) -> None:
        """Restore ratchet state from persistence."""
        self._state = new_state

    @property
    def is_initialized(self) -> bool:
        return self._state.initialized

    @property
    def public_key(self) -> bytes:
        """Our current DH ratchet public key."""
        return self._state.dh_local_public


# ---------------------------------------------------------------------------
# Re-export constant_time_compare from crypto module for backward compatibility
# ---------------------------------------------------------------------------

# constant_time_compare is now imported from presidium.core.crypto
# It is re-exported here for any code that imported it from this module.
# Use presidium.core.crypto.constant_time_compare directly in new code.



# ---------------------------------------------------------------------------
# Helper: zero bytes
# ---------------------------------------------------------------------------

def _zero_bytes(data: bytes) -> None:
    """Best-effort zeroing of bytes that may be in memory.

    Note: Python bytes are immutable, so we can't actually zero them.
    This is a best-effort approach for bytearray-backed data.
    """
    # Python bytes are immutable; we can't truly zero them.
    # The real protection comes from SecureBytes for sensitive data.
    pass
