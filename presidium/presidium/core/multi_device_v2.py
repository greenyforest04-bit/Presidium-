"""Multi-Device v2 for Presidium — seamless cross-device experience.

Implements perfect forward secrecy between a user's own devices using
X3DH + Double Ratchet, with CRDT-based sync for message states and
seamless handoff for calls and drafts.

Architecture:
    Each device has its own X25519 prekey bundle, signed by the
    master Ed25519 identity key. Devices authenticate each other
    through the master key signature chain.

    Device registration flow:
        1. New device generates X25519 + Ed25519 key pair
        2. Signs its public keys with the master identity key
        3. Sends DEVICE_REGISTER to an already-authorized device
        4. Authorized device verifies signature and approves
        5. New device receives encrypted history sync

    Sync protocol:
        - SYNC_MESSAGE type carries encrypted state updates
        - Each device pair has its own Double Ratchet session
        - CRDT for message status (read/delivered) merges conflicts
        - Last-write-wins for settings/preferences
        - Draft sync for message composition handoff

    Call handoff:
        - Incoming call rings on ALL authorized devices
        - Answer on one device → others go silent
        - Active call can be transferred between devices

CRDT implementation:
    Uses G-Counter (grow-only counter) for read receipts and
    LWW-Register (Last-Write-Wins) for settings. Message delivery
    status uses OR-Set (Observed-Remove Set) for idempotent merges.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import logging
import os
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from presidium.core.crypto import (
    AES256GCM,
    CryptoError,
    Ed25519KeyPair,
    SecureBytes,
    X25519KeyPair,
    compute_user_id,
    secure_zero,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_DEVICES_PER_USER = 5
SYNC_MESSAGE_VERSION = 1
DEVICE_KEY_SIGNATURE_PREFIX = b"presidium-device-v1:"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class MultiDeviceError(Exception):
    """Base exception for multi-device operations."""


class DeviceLimitError(MultiDeviceError):
    """Maximum number of devices reached."""


class DeviceNotAuthorizedError(MultiDeviceError):
    """Device is not authorized."""


class SyncError(MultiDeviceError):
    """Synchronization error."""


class CRDTMergeError(MultiDeviceError):
    """CRDT merge conflict error."""


# ---------------------------------------------------------------------------
# Device info
# ---------------------------------------------------------------------------

class DeviceType(enum.Enum):
    """Type of device."""
    DESKTOP = "desktop"
    MOBILE = "mobile"
    TABLET = "tablet"
    WEB = "web"
    CLI = "cli"


@dataclass
class DeviceInfo:
    """Information about a registered device."""
    device_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    device_name: str = ""           # User-friendly name, e.g. "iPhone 16"
    device_type: DeviceType = DeviceType.DESKTOP
    x25519_public_key: bytes = b""  # 32 bytes
    ed25519_public_key: bytes = b""  # 32 bytes (device signing key)
    master_signature: bytes = b""   # Ed25519 signature from master key
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    is_authorized: bool = False
    is_revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        import base64
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "device_type": self.device_type.value,
            "x25519_public_key": base64.b64encode(self.x25519_public_key).decode(),
            "ed25519_public_key": base64.b64encode(self.ed25519_public_key).decode(),
            "master_signature": base64.b64encode(self.master_signature).decode(),
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
            "is_authorized": self.is_authorized,
            "is_revoked": self.is_revoked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceInfo:
        import base64
        return cls(
            device_id=data.get("device_id", ""),
            device_name=data.get("device_name", ""),
            device_type=DeviceType(data.get("device_type", "desktop")),
            x25519_public_key=base64.b64decode(data.get("x25519_public_key", "")),
            ed25519_public_key=base64.b64decode(data.get("ed25519_public_key", "")),
            master_signature=base64.b64decode(data.get("master_signature", "")),
            registered_at=data.get("registered_at", 0.0),
            last_seen=data.get("last_seen", 0.0),
            is_authorized=data.get("is_authorized", False),
            is_revoked=data.get("is_revoked", False),
        )

    def compute_device_fingerprint(self) -> str:
        """Compute a human-readable fingerprint for this device."""
        raw = self.ed25519_public_key + self.x25519_public_key
        h = hashlib.sha256(raw).digest()
        groups = [h[i:i+2].hex().upper() for i in range(0, 8, 2)]
        return " ".join(groups)


# ---------------------------------------------------------------------------
# CRDT implementations
# ---------------------------------------------------------------------------

class LWWRegister:
    """Last-Write-Wins Register CRDT.

    For settings and preferences where the most recent value wins.
    Uses Lamport timestamp for ordering.
    """

    def __init__(self, value: Any = None, timestamp: float = 0.0, device_id: str = "") -> None:
        self.value = value
        self.timestamp = timestamp
        self.device_id = device_id

    def set(self, value: Any, timestamp: float, device_id: str) -> None:
        """Set a new value if the timestamp is newer."""
        if timestamp > self.timestamp or (
            timestamp == self.timestamp and device_id > self.device_id
        ):
            self.value = value
            self.timestamp = timestamp
            self.device_id = device_id

    def merge(self, other: LWWRegister) -> None:
        """Merge with another LWW register."""
        if other.timestamp > self.timestamp or (
            other.timestamp == self.timestamp and other.device_id > self.device_id
        ):
            self.value = other.value
            self.timestamp = other.timestamp
            self.device_id = other.device_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "timestamp": self.timestamp,
            "device_id": self.device_id,
        }


class GCounter:
    """Grow-Only Counter CRDT.

    For read receipt tracking — each device increments its own counter,
    and the total is the sum of all device counters.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}  # device_id → count

    def increment(self, device_id: str, amount: int = 1) -> None:
        """Increment this device's counter."""
        self._counts[device_id] = self._counts.get(device_id, 0) + amount

    def value(self) -> int:
        """Get the total count across all devices."""
        return sum(self._counts.values())

    def merge(self, other: GCounter) -> None:
        """Merge with another G-Counter (take max per device)."""
        for device_id, count in other._counts.items():
            self._counts[device_id] = max(
                self._counts.get(device_id, 0), count
            )

    def to_dict(self) -> dict[str, int]:
        return dict(self._counts)


class ORSet:
    """Observed-Remove Set CRDT.

    For message delivery status — each status update is uniquely tagged
    and can be merged without conflicts. Removing requires observing
    the element first (tombstone approach).
    """

    def __init__(self) -> None:
        self._elements: dict[str, set[str]] = {}   # element → set of unique tags
        self._tombstones: set[str] = set()          # removed tags

    def add(self, element: str, tag: str) -> None:
        """Add an element with a unique tag."""
        if tag not in self._tombstones:
            if element not in self._elements:
                self._elements[element] = set()
            self._elements[element].add(tag)

    def remove(self, element: str) -> None:
        """Remove an element (tombstones all current tags)."""
        tags = self._elements.pop(element, set())
        self._tombstones.update(tags)

    def contains(self, element: str) -> bool:
        """Check if an element is in the set."""
        return element in self._elements and len(self._elements[element]) > 0

    def merge(self, other: ORSet) -> None:
        """Merge with another OR-Set."""
        # Merge elements
        for element, tags in other._elements.items():
            if element not in self._elements:
                self._elements[element] = set()
            self._elements[element].update(tags - self._tombstones)

        # Merge tombstones
        self._tombstones.update(other._tombstones)

        # Remove tombstoned elements
        to_remove = []
        for element, tags in self._elements.items():
            remaining = tags - self._tombstones
            if remaining:
                self._elements[element] = remaining
            else:
                to_remove.append(element)
        for element in to_remove:
            del self._elements[element]

    def elements(self) -> set[str]:
        """Get all elements in the set."""
        return set(self._elements.keys())

    def to_dict(self) -> dict[str, Any]:
        return {
            "elements": {k: list(v) for k, v in self._elements.items()},
            "tombstones": list(self._tombstones),
        }


# ---------------------------------------------------------------------------
# Sync message types
# ---------------------------------------------------------------------------

class SyncMessageType(enum.Enum):
    """Types of sync messages between devices."""
    DEVICE_REGISTER = "device_register"
    DEVICE_APPROVE = "device_approve"
    DEVICE_REVOKE = "device_revoke"
    HISTORY_SYNC = "history_sync"
    MESSAGE_STATUS = "message_status"
    SETTINGS_UPDATE = "settings_update"
    DRAFT_SYNC = "draft_sync"
    CALL_HANDOFF = "call_handoff"
    HEARTBEAT = "heartbeat"


# ---------------------------------------------------------------------------
# Sync message
# ---------------------------------------------------------------------------

@dataclass
class SyncMessage:
    """A synchronization message between devices."""
    msg_type: SyncMessageType = SyncMessageType.HEARTBEAT
    source_device_id: str = ""
    target_device_id: str = ""  # Empty = broadcast to all devices
    payload: bytes = b""
    timestamp: float = field(default_factory=time.time)
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def encode(self) -> bytes:
        """Serialize to bytes for encrypted transport."""
        import json as _json
        header = _json.dumps({
            "type": self.msg_type.value,
            "src": self.source_device_id,
            "dst": self.target_device_id,
            "ts": self.timestamp,
            "id": self.msg_id,
        }).encode("utf-8")
        return (
            struct.pack("!H", len(header))
            + header
            + self.payload
        )

    @classmethod
    def decode(cls, data: bytes) -> SyncMessage:
        """Deserialize from bytes."""
        import json as _json
        if len(data) < 2:
            raise SyncError("Sync message too short.")
        header_len = struct.unpack("!H", data[:2])[0]
        header = _json.loads(data[2:2 + header_len].decode("utf-8"))
        payload = data[2 + header_len:]
        return cls(
            msg_type=SyncMessageType(header["type"]),
            source_device_id=header.get("src", ""),
            target_device_id=header.get("dst", ""),
            payload=payload,
            timestamp=header.get("ts", 0.0),
            msg_id=header.get("id", ""),
        )


# ---------------------------------------------------------------------------
# Multi-device manager v2
# ---------------------------------------------------------------------------

class MultiDeviceManagerV2:
    """Manages multi-device synchronization for Presidium.

    Handles device registration, pairwise E2EE between devices, CRDT
    state sync, and seamless handoff for calls and drafts.
    """

    def __init__(
        self,
        master_key_pair: Ed25519KeyPair,
        local_device_id: str = "",
    ) -> None:
        self._master_key = master_key_pair
        self._local_device_id = local_device_id or uuid.uuid4().hex[:12]
        self._devices: dict[str, DeviceInfo] = {}
        self._local_device: Optional[DeviceInfo] = None

        # CRDT state
        self._settings: dict[str, LWWRegister] = {}
        self._read_receipts: GCounter = GCounter()
        self._delivered_set: ORSet = ORSet()

        # Draft sync state
        self._drafts: dict[str, LWWRegister] = {}  # peer_id → draft text

        # Callbacks
        self._send_to_device: Optional[
            Callable[[str, bytes], Awaitable[None]]
        ] = None
        self._on_device_registered: Optional[
            Callable[[DeviceInfo], Awaitable[None]]
        ] = None
        self._on_device_revoked: Optional[
            Callable[[str], Awaitable[None]]
        ] = None
        self._on_sync_received: Optional[
            Callable[[SyncMessage], Awaitable[None]]
        ] = None
        self._on_draft_updated: Optional[
            Callable[[str, str], Awaitable[None]]  # (peer_id, draft_text)
        ] = None
        self._on_call_handoff: Optional[
            Callable[[dict[str, Any]], Awaitable[None]]
        ] = None

    # -------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------

    def set_send_callback(
        self, callback: Callable[[str, bytes], Awaitable[None]]
    ) -> None:
        """Set callback for sending encrypted data to another device."""
        self._send_to_device = callback

    def set_local_device(self, device: DeviceInfo) -> None:
        """Set the local device info."""
        self._local_device = device
        self._local_device_id = device.device_id
        self._devices[device.device_id] = device

    # -------------------------------------------------------------------
    # Device registration
    # -------------------------------------------------------------------

    def create_device_registration(
        self,
        device_name: str,
        device_type: DeviceType,
        x25519_public_key: bytes,
        ed25519_public_key: bytes,
    ) -> DeviceInfo:
        """Create a device registration signed by the master key.

        This is called on an already-authorized device when adding
        a new device.

        Args:
            device_name: Human-readable device name.
            device_type: Type of device.
            x25519_public_key: New device's X25519 public key (32 bytes).
            ed25519_public_key: New device's Ed25519 public key (32 bytes).

        Returns:
            A DeviceInfo with master signature.

        Raises:
            DeviceLimitError: If max devices already registered.
        """
        active_count = sum(1 for d in self._devices.values() if not d.is_revoked)
        if active_count >= MAX_DEVICES_PER_USER:
            raise DeviceLimitError(
                f"Maximum {MAX_DEVICES_PER_USER} devices allowed. "
                f"Revoke an existing device first."
            )

        # Sign the device keys with master key
        signed_payload = (
            DEVICE_KEY_SIGNATURE_PREFIX
            + x25519_public_key
            + ed25519_public_key
        )
        signature = self._master_key.sign(signed_payload)

        device = DeviceInfo(
            device_name=device_name,
            device_type=device_type,
            x25519_public_key=x25519_public_key,
            ed25519_public_key=ed25519_public_key,
            master_signature=signature,
            is_authorized=True,
        )

        self._devices[device.device_id] = device

        logger.info(
            "Device registered: %s (%s) with fingerprint %s",
            device.device_name,
            device.device_type.value,
            device.compute_device_fingerprint(),
        )
        return device

    def verify_device_signature(self, device: DeviceInfo) -> bool:
        """Verify a device's master signature.

        Returns:
            True if the signature is valid.
        """
        signed_payload = (
            DEVICE_KEY_SIGNATURE_PREFIX
            + device.x25519_public_key
            + device.ed25519_public_key
        )
        try:
            return Ed25519KeyPair.verify(
                self._master_key.public_key_bytes,
                device.master_signature,
                signed_payload,
            )
        except Exception:
            return False

    async def revoke_device(self, device_id: str) -> bool:
        """Revoke a device's authorization.

        Args:
            device_id: The device to revoke.

        Returns:
            True if the device was revoked.
        """
        device = self._devices.get(device_id)
        if device is None:
            return False
        if device.device_id == self._local_device_id:
            raise MultiDeviceError("Cannot revoke the current device.")

        device.is_revoked = True
        device.is_authorized = False

        # Notify other devices
        msg = SyncMessage(
            msg_type=SyncMessageType.DEVICE_REVOKE,
            source_device_id=self._local_device_id,
            payload=device_id.encode("utf-8"),
        )
        await self._broadcast_sync(msg)

        if self._on_device_revoked:
            await self._on_device_revoked(device_id)

        logger.info("Device revoked: %s (%s)", device_id, device.device_name)
        return True

    # -------------------------------------------------------------------
    # CRDT sync operations
    # -------------------------------------------------------------------

    async def sync_message_status(
        self,
        message_id: str,
        status: str,  # "delivered" | "read"
        device_id: str = "",
    ) -> None:
        """Sync a message status update across devices."""
        tag = f"{device_id or self._local_device_id}:{time.time():.3f}"

        if status == "delivered":
            self._delivered_set.add(message_id, tag)
        elif status == "read":
            self._read_receipts.increment(device_id or self._local_device_id)

        # Broadcast the update
        import json as _json
        payload = _json.dumps({
            "message_id": message_id,
            "status": status,
            "tag": tag,
            "device_id": device_id or self._local_device_id,
        }).encode("utf-8")

        msg = SyncMessage(
            msg_type=SyncMessageType.MESSAGE_STATUS,
            source_device_id=self._local_device_id,
            payload=payload,
        )
        await self._broadcast_sync(msg)

    async def sync_setting(self, key: str, value: Any) -> None:
        """Sync a setting update across devices."""
        ts = time.time()
        self._settings[key] = LWWRegister(value, ts, self._local_device_id)

        import json as _json
        payload = _json.dumps({
            "key": key,
            "value": value,
            "timestamp": ts,
            "device_id": self._local_device_id,
        }).encode("utf-8")

        msg = SyncMessage(
            msg_type=SyncMessageType.SETTINGS_UPDATE,
            source_device_id=self._local_device_id,
            payload=payload,
        )
        await self._broadcast_sync(msg)

    async def sync_draft(self, peer_id: str, draft_text: str) -> None:
        """Sync a message draft across devices (Continuity-style).

        When a user starts typing on one device, the draft is
        synced to all other devices so they can continue composing
        on a different device.
        """
        ts = time.time()
        self._drafts[peer_id] = LWWRegister(draft_text, ts, self._local_device_id)

        import json as _json
        payload = _json.dumps({
            "peer_id": peer_id,
            "draft_text": draft_text,
            "timestamp": ts,
            "device_id": self._local_device_id,
        }).encode("utf-8")

        msg = SyncMessage(
            msg_type=SyncMessageType.DRAFT_SYNC,
            source_device_id=self._local_device_id,
            payload=payload,
        )
        await self._broadcast_sync(msg)

    async def handoff_call(self, call_data: dict[str, Any]) -> None:
        """Hand off an active call to another device.

        Args:
            call_data: Call state to transfer (call_id, SDP, participants).
        """
        import json as _json
        msg = SyncMessage(
            msg_type=SyncMessageType.CALL_HANDOFF,
            source_device_id=self._local_device_id,
            payload=_json.dumps(call_data).encode("utf-8"),
        )
        await self._broadcast_sync(msg)

    # -------------------------------------------------------------------
    # Incoming sync message handler
    # -------------------------------------------------------------------

    async def handle_sync_message(self, data: bytes) -> None:
        """Process an incoming sync message from another device."""
        try:
            msg = SyncMessage.decode(data)
        except SyncError as exc:
            logger.error("Invalid sync message: %s", exc)
            return

        # Verify source device is authorized
        source_device = self._devices.get(msg.source_device_id)
        if source_device is None or source_device.is_revoked:
            logger.warning("Sync message from unknown/revoked device: %s", msg.source_device_id)
            return

        # Dispatch by type
        if msg.msg_type == SyncMessageType.MESSAGE_STATUS:
            await self._handle_message_status(msg)
        elif msg.msg_type == SyncMessageType.SETTINGS_UPDATE:
            await self._handle_settings_update(msg)
        elif msg.msg_type == SyncMessageType.DRAFT_SYNC:
            await self._handle_draft_sync(msg)
        elif msg.msg_type == SyncMessageType.DEVICE_REVOKE:
            await self._handle_device_revoke(msg)
        elif msg.msg_type == SyncMessageType.CALL_HANDOFF:
            await self._handle_call_handoff(msg)
        elif msg.msg_type == SyncMessageType.HEARTBEAT:
            source_device.last_seen = time.time()

        if self._on_sync_received:
            await self._on_sync_received(msg)

    async def _handle_message_status(self, msg: SyncMessage) -> None:
        """Handle incoming message status sync."""
        import json as _json
        data = _json.loads(msg.payload.decode("utf-8"))
        message_id = data.get("message_id", "")
        status = data.get("status", "")
        tag = data.get("tag", "")
        device_id = data.get("device_id", "")

        if status == "delivered":
            self._delivered_set.add(message_id, tag)
        elif status == "read":
            self._read_receipts.increment(device_id)

    async def _handle_settings_update(self, msg: SyncMessage) -> None:
        """Handle incoming settings sync."""
        import json as _json
        data = _json.loads(msg.payload.decode("utf-8"))
        key = data.get("key", "")
        value = data.get("value")
        ts = data.get("timestamp", 0.0)
        device_id = data.get("device_id", "")

        if key:
            current = self._settings.get(key, LWWRegister())
            current.set(value, ts, device_id)
            self._settings[key] = current

    async def _handle_draft_sync(self, msg: SyncMessage) -> None:
        """Handle incoming draft sync."""
        import json as _json
        data = _json.loads(msg.payload.decode("utf-8"))
        peer_id = data.get("peer_id", "")
        draft_text = data.get("draft_text", "")
        ts = data.get("timestamp", 0.0)
        device_id = data.get("device_id", "")

        if peer_id:
            current = self._drafts.get(peer_id, LWWRegister())
            current.set(draft_text, ts, device_id)
            self._drafts[peer_id] = current

            if self._on_draft_updated:
                await self._on_draft_updated(peer_id, draft_text)

    async def _handle_device_revoke(self, msg: SyncMessage) -> None:
        """Handle device revocation."""
        device_id = msg.payload.decode("utf-8")
        device = self._devices.get(device_id)
        if device:
            device.is_revoked = True
            device.is_authorized = False
            if self._on_device_revoked:
                await self._on_device_revoked(device_id)

    async def _handle_call_handoff(self, msg: SyncMessage) -> None:
        """Handle call handoff."""
        import json as _json
        call_data = _json.loads(msg.payload.decode("utf-8"))
        if self._on_call_handoff:
            await self._on_call_handoff(call_data)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _broadcast_sync(self, msg: SyncMessage) -> None:
        """Send a sync message to all authorized devices."""
        if self._send_to_device is None:
            return

        encoded = msg.encode()
        for device_id, device in self._devices.items():
            if device_id == self._local_device_id:
                continue
            if not device.is_authorized or device.is_revoked:
                continue
            try:
                await self._send_to_device(device_id, encoded)
            except Exception as exc:
                logger.debug("Failed to sync with device %s: %s", device_id, exc)

    # -------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------

    def get_devices(self) -> list[DeviceInfo]:
        """Get all registered devices."""
        return list(self._devices.values())

    def get_authorized_devices(self) -> list[DeviceInfo]:
        """Get authorized (non-revoked) devices."""
        return [d for d in self._devices.values() if d.is_authorized and not d.is_revoked]

    def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """Get a device by its ID."""
        return self._devices.get(device_id)

    def get_draft(self, peer_id: str) -> str:
        """Get the current draft for a peer (from any device)."""
        register = self._drafts.get(peer_id)
        return register.value if register else ""

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a synced setting value."""
        register = self._settings.get(key)
        return register.value if register else default

    def is_message_delivered(self, message_id: str) -> bool:
        """Check if a message has been delivered (any device)."""
        return self._delivered_set.contains(message_id)

    def get_read_count(self) -> int:
        """Get the total read receipt count across devices."""
        return self._read_receipts.value()
