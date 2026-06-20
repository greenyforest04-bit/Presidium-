"""Identity management for Presidium.

Handles:
- Ed25519 key pair generation and storage
- User ID computation (SHA-256 of public key, first 16 bytes)
- Encrypted on-disk persistence of private keys
- Loading/unlocking identity with user password
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import aiofiles

from presidium.core.crypto import (
    Ed25519KeyPair,
    compute_user_id,
    CryptoError,
    SecureBytes,
)
from presidium.utils.config import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class IdentityError(Exception):
    """Base exception for identity operations."""


class IdentityNotFoundError(IdentityError):
    """No identity file found on disk."""


class IdentityCorruptedError(IdentityError):
    """Identity file is corrupted or cannot be parsed."""


class IdentityLockedError(IdentityError):
    """Identity is locked and must be unlocked with a password."""


class WrongPasswordError(IdentityError):
    """Incorrect password provided for identity decryption."""


# ---------------------------------------------------------------------------
# Identity manager
# ---------------------------------------------------------------------------

class IdentityManager:
    """Manages the local node's Ed25519 identity.

    The identity consists of:
    - An Ed25519 key pair (private key encrypted on disk with Argon2-derived key)
    - A User ID derived from the public key
    - An optional display nickname

    Typical lifecycle:
        1. Check if identity exists → load or create
        2. Create: generate key pair, encrypt with password, save to disk
        3. Load: decrypt with password, restore key pair
        4. Access user_id, public_key_bytes for networking
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._key_pair: Optional[Ed25519KeyPair] = None
        self._user_id: Optional[str] = None
        self._nickname: Optional[str] = None
        self._locked = True

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Check if an identity file exists on disk."""
        return self._config.key_file_path.exists()

    @property
    def is_unlocked(self) -> bool:
        """Check if the identity is currently unlocked (key in memory)."""
        return not self._locked and self._key_pair is not None

    @property
    def user_id(self) -> str:
        """Get the User ID. Raises if not unlocked."""
        if self._user_id is None:
            raise IdentityLockedError("Identity is not unlocked.")
        return self._user_id

    @property
    def public_key_bytes(self) -> bytes:
        """Get the raw Ed25519 public key (32 bytes). Raises if not unlocked."""
        if self._key_pair is None:
            raise IdentityLockedError("Identity is not unlocked.")
        return self._key_pair.public_key_bytes

    @property
    def nickname(self) -> Optional[str]:
        """Get the display nickname (may be None)."""
        return self._nickname

    @nickname.setter
    def nickname(self, value: str) -> None:
        """Set display nickname."""
        from presidium.utils.validators import sanitize_nickname
        self._nickname = sanitize_nickname(value)

    @property
    def key_pair(self) -> Ed25519KeyPair:
        """Access the underlying Ed25519 key pair. Raises if not unlocked."""
        if self._key_pair is None:
            raise IdentityLockedError("Identity is not unlocked.")
        return self._key_pair

    # -----------------------------------------------------------------------
    # Identity creation
    # -----------------------------------------------------------------------

    async def create_identity(self, password: str, nickname: Optional[str] = None) -> str:
        """Generate a new Ed25519 identity, encrypt and save to disk.

        Args:
            password: User password for encrypting the private key.
            nickname: Optional display name.

        Returns:
            The computed User ID (32 hex chars).

        Raises:
            IdentityError: If an identity already exists or creation fails.
        """
        if self.is_initialized:
            raise IdentityError("Identity already exists. Use unlock() instead.")

        if not password or len(password) < 6:
            raise IdentityError("Password must be at least 6 characters.")

        self._config.ensure_data_dir()

        # Generate new Ed25519 key pair
        self._key_pair = Ed25519KeyPair()
        self._user_id = compute_user_id(self._key_pair.public_key_bytes)

        if nickname:
            from presidium.utils.validators import sanitize_nickname
            self._nickname = sanitize_nickname(nickname)

        # Encrypt and save
        try:
            encrypted = self._key_pair.to_encrypted_pem(password)
            async with aiofiles.open(self._config.key_file_path, "wb") as f:
                await f.write(encrypted)
        except CryptoError as exc:
            self._key_pair = None
            self._user_id = None
            raise IdentityError(f"Failed to encrypt and save identity: {exc}") from exc

        self._locked = False
        logger.info("New identity created: user_id=%s", self._user_id)
        return self._user_id

    # -----------------------------------------------------------------------
    # Identity loading / unlocking
    # -----------------------------------------------------------------------

    async def unlock(self, password: str) -> str:
        """Decrypt and load the identity from disk.

        Args:
            password: User password for decryption.

        Returns:
            The computed User ID.

        Raises:
            IdentityNotFoundError: No identity file on disk.
            WrongPasswordError: Incorrect password.
            IdentityCorruptedError: File cannot be parsed.
        """
        if not self.is_initialized:
            raise IdentityNotFoundError(
                f"No identity file at {self._config.key_file_path}. "
                "Call create_identity() first."
            )

        try:
            async with aiofiles.open(self._config.key_file_path, "rb") as f:
                encrypted_data = await f.read()
        except OSError as exc:
            raise IdentityCorruptedError(f"Cannot read identity file: {exc}") from exc

        if len(encrypted_data) < 64:
            raise IdentityCorruptedError("Identity file is too short to be valid.")

        try:
            self._key_pair = Ed25519KeyPair.from_encrypted_pem(encrypted_data, password)
        except CryptoError as exc:
            raise WrongPasswordError(
                "Failed to decrypt identity. Wrong password?"
            ) from exc

        self._user_id = compute_user_id(self._key_pair.public_key_bytes)
        self._locked = False
        logger.info("Identity unlocked: user_id=%s", self._user_id)
        return self._user_id

    # -----------------------------------------------------------------------
    # Locking / cleanup
    # -----------------------------------------------------------------------

    def lock(self) -> None:
        """Lock the identity, zeroing private key material from memory."""
        if self._key_pair is not None:
            # Best-effort zeroing of key bytes
            try:
                raw = self._key_pair.private_key_bytes
                from presidium.core.crypto import secure_zero
                secure_zero(bytearray(raw))
            except Exception:
                pass
        self._key_pair = None
        self._user_id = None
        self._nickname = None
        self._locked = True
        logger.info("Identity locked and key material zeroed.")

    # -----------------------------------------------------------------------
    # Export helpers
    # -----------------------------------------------------------------------

    def get_shareable_contact(self) -> dict[str, str]:
        """Return a dict with user_id and public_key_b64 for sharing.

        Raises:
            IdentityLockedError: If identity is not unlocked.
        """
        if not self.is_unlocked:
            raise IdentityLockedError("Identity must be unlocked to share contact info.")
        import base64
        return {
            "user_id": self._user_id,  # type: ignore[dict-item]
            "public_key": base64.b64encode(self.public_key_bytes).decode("ascii"),
            "nickname": self._nickname or "",
        }

    def generate_qr_data(self) -> str:
        """Generate a compact string for QR code encoding.

        Format: presidium://<user_id>:<base64_public_key>
        """
        import base64
        if not self.is_unlocked:
            raise IdentityLockedError("Identity must be unlocked for QR generation.")
        pk_b64 = base64.b64encode(self.public_key_bytes).decode("ascii")
        return f"presidium://{self._user_id}:{pk_b64}"

    @staticmethod
    def parse_qr_data(qr_string: str) -> tuple[str, bytes]:
        """Parse a QR code string and return (user_id, public_key_bytes).

        Args:
            qr_string: String in format presidium://<user_id>:<base64_public_key>

        Returns:
            Tuple of (user_id, public_key_bytes).

        Raises:
            IdentityError: If the string is malformed.
        """
        import base64
        if not qr_string.startswith("presidium://"):
            raise IdentityError(f"Invalid QR data: must start with 'presidium://'")
        payload = qr_string[len("presidium://"):]
        if ":" not in payload:
            raise IdentityError("Invalid QR data: missing ':' separator.")
        user_id, pk_b64 = payload.split(":", 1)
        try:
            pk_bytes = base64.b64decode(pk_b64)
        except Exception as exc:
            raise IdentityError(f"Invalid base64 in QR data: {exc}") from exc
        if len(pk_bytes) != 32:
            raise IdentityError(f"Invalid public key length: {len(pk_bytes)}, expected 32.")
        return user_id, pk_bytes
