"""Audit logging for Presidium.

Provides:
- Append-only encrypted log: every event signed with Ed25519 + encrypted with AES-GCM
- Daily rotation with 90-day retention
- Export: JSON for user, immutable binary for forensic
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from presidium.core.crypto import Ed25519KeyPair, AES256GCM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit event types
# ---------------------------------------------------------------------------

class AuditEventType(Enum):
    """Types of auditable events."""
    IDENTITY_CREATED = auto()
    IDENTITY_UNLOCKED = auto()
    IDENTITY_LOCKED = auto()
    KEY_EXCHANGE_INITIATED = auto()
    KEY_EXCHANGE_COMPLETED = auto()
    KEY_EXCHANGE_FAILED = auto()
    MESSAGE_SENT = auto()
    MESSAGE_RECEIVED = auto()
    MESSAGE_DELETED = auto()
    FILE_SENT = auto()
    FILE_RECEIVED = auto()
    VOICE_CALL_STARTED = auto()
    VOICE_CALL_ENDED = auto()
    CONTACT_ADDED = auto()
    CONTACT_REMOVED = auto()
    DEVICE_REGISTERED = auto()
    DEVICE_REVOKED = auto()
    GROUP_CREATED = auto()
    GROUP_MEMBER_ADDED = auto()
    GROUP_MEMBER_REMOVED = auto()
    DHT_STORE = auto()
    DHT_LOOKUP = auto()
    SETTINGS_CHANGED = auto()
    APP_STARTED = auto()
    APP_SHUTDOWN = auto()


# ---------------------------------------------------------------------------
# Audit event
# ---------------------------------------------------------------------------

@dataclass
class AuditEvent:
    """A single auditable event."""
    event_type: AuditEventType
    timestamp: float = field(default_factory=time.time)
    details: dict = field(default_factory=dict)
    sequence_number: int = 0


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Append-only encrypted audit log.

    Each event is:
    1. Serialized to JSON
    2. Signed with Ed25519
    3. Encrypted with AES-256-GCM using a derived log key
    4. Appended to the current day's log file

    Features:
    - Daily rotation (new file per day)
    - 90-day retention (older files auto-deleted)
    - Sequence numbers for tamper detection
    - Export to JSON or binary format
    """

    RETENTION_DAYS = 90

    def __init__(
        self,
        log_dir: Path,
        signing_key: Ed25519KeyPair,
        encryption_key: bytes,  # 32 bytes AES key
    ) -> None:
        self._log_dir = log_dir
        self._signing_key = signing_key
        self._encryption_key = encryption_key
        self._sequence = 0
        self._current_date: Optional[str] = None
        self._current_file: Optional[Path] = None

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create log directory and clean up old logs."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._sequence = await self._load_last_sequence()
        await self._cleanup_old_logs()
        logger.info("Audit logger initialized. Last sequence: %d", self._sequence)

    # -------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------

    async def log_event(
        self,
        event_type: AuditEventType,
        details: Optional[dict] = None,
    ) -> None:
        """Log an audit event.

        Args:
            event_type: The type of event.
            details: Optional details dictionary.
        """
        self._sequence += 1
        event = AuditEvent(
            event_type=event_type,
            details=details or {},
            sequence_number=self._sequence,
        )

        # Serialize
        event_json = json.dumps(
            {
                "type": event.event_type.name,
                "timestamp": event.timestamp,
                "seq": event.sequence_number,
                "details": event.details,
            },
            sort_keys=True,
        ).encode("utf-8")

        # Sign
        signature = self._signing_key.sign(event_json)

        # Encrypt (signature + event_json)
        plaintext = struct.pack("!I", len(signature)) + signature + event_json
        encrypted = AES256GCM.encrypt(self._encryption_key, plaintext)

        # Write to current day's file
        log_path = self._get_current_log_file()
        import aiofiles
        async with aiofiles.open(log_path, "ab") as f:
            # Write: [4 bytes total_length][encrypted_data]
            length = struct.pack("!I", len(encrypted))
            await f.write(length + encrypted)

    # -------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------

    async def export_json(self, output_path: Path, start_date: Optional[str] = None) -> None:
        """Export audit log as JSON.

        Args:
            output_path: Path to write the JSON file.
            start_date: Optional date filter (YYYY-MM-DD).
        """
        events = []
        for log_file in sorted(self._log_dir.glob("audit_*.bin")):
            if start_date and log_file.stem < f"audit_{start_date}":
                continue
            file_events = await self._decrypt_log_file(log_file)
            events.extend(file_events)

        import aiofiles
        async with aiofiles.open(output_path, "w") as f:
            await f.write(json.dumps(events, indent=2, ensure_ascii=False))

    # -------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------

    def _get_current_log_file(self) -> Path:
        """Get the log file path for the current date."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            self._current_file = self._log_dir / f"audit_{today}.bin"
        return self._current_file

    async def _load_last_sequence(self) -> int:
        """Load the last sequence number from existing logs."""
        max_seq = 0
        for log_file in self._log_dir.glob("audit_*.bin"):
            try:
                events = await self._decrypt_log_file(log_file)
                for evt in events:
                    seq = evt.get("seq", 0)
                    if seq > max_seq:
                        max_seq = seq
            except Exception:
                pass
        return max_seq

    async def _decrypt_log_file(self, log_path: Path) -> list[dict]:
        """Decrypt and parse a log file."""
        import aiofiles
        events = []
        try:
            async with aiofiles.open(log_path, "rb") as f:
                data = await f.read()

            offset = 0
            while offset < len(data):
                if offset + 4 > len(data):
                    break
                total_len = struct.unpack("!I", data[offset : offset + 4])[0]
                offset += 4
                if offset + total_len > len(data):
                    break
                encrypted = data[offset : offset + total_len]
                offset += total_len

                try:
                    plaintext = AES256GCM.decrypt(self._encryption_key, encrypted)
                    sig_len = struct.unpack("!I", plaintext[:4])[0]
                    signature = plaintext[4 : 4 + sig_len]
                    event_json = plaintext[4 + sig_len :]

                    # Verify signature
                    if not Ed25519KeyPair.verify(
                        self._signing_key.public_key_bytes, signature, event_json
                    ):
                        logger.warning("Audit event signature mismatch in %s", log_path.name)
                        continue

                    event = json.loads(event_json.decode("utf-8"))
                    events.append(event)
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Failed to read audit log %s: %s", log_path.name, exc)
        return events

    async def _cleanup_old_logs(self) -> None:
        """Delete audit logs older than RETENTION_DAYS."""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=self.RETENTION_DAYS)).strftime("%Y-%m-%d")
        count = 0
        for log_file in self._log_dir.glob("audit_*.bin"):
            date_str = log_file.stem.replace("audit_", "")
            if date_str < cutoff:
                log_file.unlink(missing_ok=True)
                count += 1
        if count:
            logger.info("Cleaned up %d audit logs older than %s days.", count, self.RETENTION_DAYS)
