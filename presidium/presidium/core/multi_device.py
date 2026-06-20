"""Multi-device support for Presidium.

Provides:
- One identity (master key) with multiple device keys
- Device key = Ed25519, signed by master key
- Cross-device sync: encrypted sync messages between own devices
- Device revocation: DEVICE_REVOCATION certificate signed by master key
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from presidium.core.crypto import Ed25519KeyPair, AES256GCM, compute_user_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MultiDeviceError(Exception):
    """Base multi-device error."""


class DeviceNotAuthorizedError(MultiDeviceError):
    """Device is not authorized."""


class DeviceRevokedError(MultiDeviceError):
    """Device has been revoked."""


# ---------------------------------------------------------------------------
# Device record
# ---------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    """Information about a device linked to an identity."""
    device_id: str  # first 16 hex chars of SHA-256(device_ed25519_pk)
    device_public_key: bytes  # 32 bytes Ed25519
    device_name: str  # e.g. "iPhone 15", "Desktop Linux"
    signed_at: float = field(default_factory=time.time)
    is_revoked: bool = False
    revoked_at: float = 0.0
    signature: bytes = b""  # Ed25519 master key signature of (device_id + device_public_key)


# ---------------------------------------------------------------------------
# Revocation certificate
# ---------------------------------------------------------------------------

@dataclass
class RevocationCertificate:
    """A certificate revoking a device, signed by the master key."""
    master_user_id: str
    device_id: str
    revoked_at: float
    reason: str = ""
    signature: bytes = b""  # Ed25519 master key signature


# ---------------------------------------------------------------------------
# Multi-Device Manager
# ---------------------------------------------------------------------------

class MultiDeviceManager:
    """Manages multiple devices for a single identity.

    The master key is the user's primary Ed25519 key pair. Each device
    generates its own Ed25519 key pair and registers it with the master
    by having the master sign the device's public key.

    Device flow:
    1. New device generates Ed25519 key pair
    2. Device creates a registration request (device_pk + name)
    3. Master key signs the device public key → DeviceInfo
    4. Device receives the signed registration → can now authenticate
    5. To revoke: master creates RevocationCertificate, broadcasts to contacts
    """

    def __init__(
        self,
        master_key_pair: Ed25519KeyPair,
        master_user_id: str,
    ) -> None:
        self._master_key = master_key_pair
        self._master_user_id = master_user_id
        self._devices: dict[str, DeviceInfo] = {}
        self._current_device_id: Optional[str] = None

    # -------------------------------------------------------------------
    # Device registration
    # -------------------------------------------------------------------

    def register_device(
        self,
        device_public_key: bytes,
        device_name: str = "",
    ) -> DeviceInfo:
        """Register a new device, signed by the master key.

        Args:
            device_public_key: 32-byte Ed25519 public key of the new device.
            device_name: Human-readable device name.

        Returns:
            DeviceInfo with the master key's signature.
        """
        device_id = compute_user_id(device_public_key)

        # Sign: device_id + device_public_key
        signed_data = device_id.encode() + device_public_key
        signature = self._master_key.sign(signed_data)

        device = DeviceInfo(
            device_id=device_id,
            device_public_key=device_public_key,
            device_name=device_name,
            signed_at=time.time(),
            signature=signature,
        )
        self._devices[device_id] = device

        logger.info("Device registered: %s (%s)", device_name or device_id[:8], device_id[:8])
        return device

    def set_current_device(self, device_id: str) -> None:
        """Set the current device ID (this instance)."""
        self._current_device_id = device_id

    # -------------------------------------------------------------------
    # Device verification
    # -------------------------------------------------------------------

    @staticmethod
    def verify_device(
        master_public_key_bytes: bytes,
        device_info: DeviceInfo,
    ) -> bool:
        """Verify that a device was signed by the master key.

        Args:
            master_public_key_bytes: 32-byte Ed25519 master public key.
            device_info: The device info to verify.

        Returns:
            True if the signature is valid.
        """
        signed_data = device_info.device_id.encode() + device_info.device_public_key
        return Ed25519KeyPair.verify(master_public_key_bytes, device_info.signature, signed_data)

    # -------------------------------------------------------------------
    # Device revocation
    # -------------------------------------------------------------------

    def revoke_device(self, device_id: str, reason: str = "") -> RevocationCertificate:
        """Revoke a device by creating a revocation certificate.

        Args:
            device_id: The device ID to revoke.
            reason: Reason for revocation.

        Returns:
            RevocationCertificate signed by the master key.
        """
        device = self._devices.get(device_id)
        if device is None:
            raise MultiDeviceError(f"Device {device_id} not found.")

        if device.is_revoked:
            raise MultiDeviceError(f"Device {device_id} is already revoked.")

        # Create revocation certificate
        revoked_at = time.time()
        cert_data = (
            self._master_user_id.encode()
            + device_id.encode()
            + str(revoked_at).encode()
            + reason.encode()
        )
        signature = self._master_key.sign(cert_data)

        cert = RevocationCertificate(
            master_user_id=self._master_user_id,
            device_id=device_id,
            revoked_at=revoked_at,
            reason=reason,
            signature=signature,
        )

        # Mark device as revoked
        device.is_revoked = True
        device.revoked_at = revoked_at

        logger.warning("Device revoked: %s. Reason: %s", device_id[:8], reason or "none")
        return cert

    @staticmethod
    def verify_revocation(
        master_public_key_bytes: bytes,
        cert: RevocationCertificate,
    ) -> bool:
        """Verify a revocation certificate."""
        cert_data = (
            cert.master_user_id.encode()
            + cert.device_id.encode()
            + str(cert.revoked_at).encode()
            + cert.reason.encode()
        )
        return Ed25519KeyPair.verify(master_public_key_bytes, cert.signature, cert_data)

    # -------------------------------------------------------------------
    # Cross-device sync
    # -------------------------------------------------------------------

    async def create_sync_message(self, data: bytes, target_device_public_key: bytes) -> bytes:
        """Create an encrypted sync message for another own device.

        The sync message is encrypted with a shared secret derived from
        the master key and the target device's public key.

        PHASE3: simplified — uses ECDH with master key as the DH key.
        In production, would use proper X3DH.
        """
        from presidium.core.crypto import X25519KeyPair, derive_session_key
        # Convert Ed25519 keys to X25519 for DH (simplified — in production use proper conversion)
        ephemeral = X25519KeyPair()
        shared = ephemeral.shared_secret(target_device_public_key)
        session_key = derive_session_key(bytes(shared), info=b"presidium-device-sync-v1")

        encrypted = AES256GCM.encrypt(bytes(session_key), data)
        # Prepend ephemeral public key
        return ephemeral.public_key_bytes + encrypted

    # -------------------------------------------------------------------
    # Getters
    # -------------------------------------------------------------------

    def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        return self._devices.get(device_id)

    def get_all_devices(self) -> list[DeviceInfo]:
        return list(self._devices.values())

    def get_active_devices(self) -> list[DeviceInfo]:
        return [d for d in self._devices.values() if not d.is_revoked]

    def is_device_revoked(self, device_id: str) -> bool:
        device = self._devices.get(device_id)
        return device.is_revoked if device else True

    @property
    def current_device_id(self) -> Optional[str]:
        return self._current_device_id
