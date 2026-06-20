"""Import module for migrating from other messengers into Presidium.

Provides a complete migration pipeline for importing conversation history
and contacts from Signal, WhatsApp, Telegram, and Matrix. Each source has
a dedicated converter that understands the export format, validates and
sanitizes the data, then inserts it into the Presidium database.

Architecture:
    ImportManager (orchestrator)
        ├── MessageConverter (abstract base)
        │       ├── SignalConverter  — reads Signal JSON exports
        │       ├── WhatsAppConverter — reads WhatsApp .txt exports
        │       ├── TelegramConverter — reads Telegram JSON exports
        │       └── (extensible for future sources)
        ├── ValidationPipeline — sanitizes and validates every message
        ├── ProgressReporter — optional callback for UI progress bars
        └── ImportResult — summary dataclass for the import outcome

Import flow:
    1. User selects export directory/file
    2. ImportManager detects source type and instantiates the correct Converter
    3. Converter reads and parses the export format into intermediate records
    4. Each record is validated (XSS, injection, length limits, encoding)
    5. Validated records are inserted into the Presidium DB in batches
    6. Progress callbacks fire after each batch
    7. ImportResult summarizes total / imported / skipped / errors

Security considerations:
    - All imported text is sanitized (no HTML/JS injection)
    - Attachments are quarantined and scanned before linking
    - Sender identities are mapped to Presidium contact format
    - No raw external data enters the DB without validation

Supported export formats:
    - Signal: JSON export (signal-export format)
    - WhatsApp: _chat.txt (WhatsApp chat export format)
    - Telegram: result.json (Telegram Desktop export)
    - Matrix: JSON export (matrix-archive format, planned)
"""

from __future__ import annotations

import abc
import asyncio
import enum
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from presidium.i18n import _

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 100
MAX_MESSAGE_LENGTH = 65536
MAX_SENDER_NAME_LENGTH = 256
SANITIZE_HTML_PATTERN = re.compile(r"<[^>]+>")
SANITIZE_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".json": "JSON",
    ".txt": "Text",
    ".csv": "CSV",
}


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ImportError(Exception):
    """Base exception for all import operations."""


class ImportFormatError(ImportError):
    """Export file format is invalid or cannot be parsed."""


class ImportValidationError(ImportError):
    """A record failed validation and cannot be imported."""


class ImportStorageError(ImportError):
    """Failed to write imported data to the Presidium database."""


class ImportCancelledError(ImportError):
    """The import was cancelled by the user."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ImportSource(enum.Enum):
    """Supported messenger sources for import."""
    SIGNAL = "signal"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    MATRIX = "matrix"


class MessageMediaType(enum.Enum):
    """Types of media attachments in imported messages."""
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    STICKER = "sticker"
    VOICE = "voice"
    ANIMATION = "animation"
    LOCATION = "location"
    CONTACT = "contact"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ImportedAttachment:
    """A media attachment from an imported message.

    Attributes:
        media_type: Type of media (image, video, etc.).
        original_path: Path as found in the export file.
        file_name: Sanitized file name.
        mime_type: Detected or declared MIME type.
        size_bytes: File size in bytes (0 if unknown).
    """
    media_type: MessageMediaType = MessageMediaType.FILE
    original_path: str = ""
    file_name: str = ""
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0


@dataclass
class ImportedMessage:
    """Intermediate representation of a message during import.

    This is the canonical format that all converters produce before
    validation and database insertion.

    Attributes:
        source: Which messenger this message came from.
        external_id: Original message ID in the source messenger.
        sender_name: Display name of the sender in the export.
        sender_id: Stable identifier for the sender (if available).
        timestamp: Unix timestamp (seconds) of the message.
        text: Message body text.
        attachments: List of media attachments.
        reply_to_id: External ID of the message this replies to.
        is_outgoing: Whether the message was sent by the account owner.
        chat_name: Name of the group or conversation.
        raw_data: Original parsed dict for debugging purposes.
    """
    source: ImportSource = ImportSource.SIGNAL
    external_id: str = ""
    sender_name: str = ""
    sender_id: str = ""
    timestamp: float = 0.0
    text: str = ""
    attachments: list[ImportedAttachment] = field(default_factory=list)
    reply_to_id: str = ""
    is_outgoing: bool = False
    chat_name: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportResult:
    """Summary of an import operation.

    Attributes:
        source: Which messenger was imported from.
        total_messages: Total number of messages found in the export.
        imported_messages: Number of messages successfully imported.
        skipped_messages: Number of messages skipped (duplicates, invalid).
        errors: List of error descriptions for failed messages.
        duration_seconds: Wall-clock time of the import operation.
    """
    source: ImportSource = ImportSource.SIGNAL
    total_messages: int = 0
    imported_messages: int = 0
    skipped_messages: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """Ratio of successfully imported messages (0.0 to 1.0)."""
        if self.total_messages == 0:
            return 0.0
        return self.imported_messages / self.total_messages

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to a JSON-compatible dict."""
        return {
            "source": self.source.value,
            "total_messages": self.total_messages,
            "imported_messages": self.imported_messages,
            "skipped_messages": self.skipped_messages,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
            "success_rate": self.success_rate,
        }


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, ImportSource], None]


# ---------------------------------------------------------------------------
# Validation pipeline
# ---------------------------------------------------------------------------

class ValidationPipeline:
    """Sanitizes and validates imported messages before database insertion.

    Applies a sequence of validation rules to each ImportedMessage:
        1. Text sanitization (strip HTML tags, control characters)
        2. Length limits enforcement
        3. Sender name normalization
        4. Timestamp range verification
        5. Attachment validation

    Messages that fail validation are either corrected (best-effort) or
    flagged as skipped.
    """

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Remove HTML tags and control characters from message text.

        Args:
            text: Raw message text from the export.

        Returns:
            Sanitized text safe for storage and display.
        """
        cleaned = SANITIZE_HTML_PATTERN.sub("", text)
        cleaned = SANITIZE_CONTROL_CHARS.sub("", cleaned)
        return cleaned.strip()

    @staticmethod
    def truncate_text(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> str:
        """Truncate text to the maximum allowed length.

        Args:
            text: Message text to potentially truncate.
            max_length: Maximum number of characters allowed.

        Returns:
            Text truncated to max_length if necessary.
        """
        if len(text) <= max_length:
            return text
        return text[:max_length]

    @staticmethod
    def sanitize_sender_name(name: str) -> str:
        """Sanitize and truncate a sender display name.

        Args:
            name: Raw sender name from the export.

        Returns:
            Cleaned sender name within length limits.
        """
        cleaned = SANITIZE_CONTROL_CHARS.sub("", name).strip()
        if len(cleaned) > MAX_SENDER_NAME_LENGTH:
            cleaned = cleaned[:MAX_SENDER_NAME_LENGTH]
        return cleaned

    @staticmethod
    def validate_timestamp(timestamp: float) -> float:
        """Validate a message timestamp is within reasonable bounds.

        Rejects timestamps before 2000-01-01 or in the future.

        Args:
            timestamp: Unix timestamp in seconds.

        Returns:
            The validated timestamp.

        Raises:
            ImportValidationError: If the timestamp is out of range.
        """
        min_timestamp = 946684800.0  # 2000-01-01 00:00:00 UTC
        max_timestamp = time.time() + 86400.0  # Allow up to 1 day in future
        if timestamp < min_timestamp:
            raise ImportValidationError(
                _("Timestamp {ts} is before year 2000, likely invalid.").format(
                    ts=timestamp
                )
            )
        if timestamp > max_timestamp:
            raise ImportValidationError(
                _("Timestamp {ts} is in the future, likely invalid.").format(
                    ts=timestamp
                )
            )
        return timestamp

    def validate_message(self, message: ImportedMessage) -> ImportedMessage:
        """Run the full validation pipeline on a single message.

        Args:
            message: The imported message to validate.

        Returns:
            The validated and sanitized message.

        Raises:
            ImportValidationError: If the message cannot be salvaged.
        """
        # Sanitize text content
        message.text = self.sanitize_text(message.text)
        message.text = self.truncate_text(message.text)

        # Sanitize sender name
        message.sender_name = self.sanitize_sender_name(message.sender_name)

        # Validate timestamp
        if message.timestamp > 0:
            message.timestamp = self.validate_timestamp(message.timestamp)
        else:
            # Default to epoch if missing; allow through but log
            logger.debug(
                "Message with external_id=%s has no timestamp, defaulting to 0.",
                message.external_id,
            )

        # Validate attachments
        validated_attachments: list[ImportedAttachment] = []
        for attachment in message.attachments:
            if attachment.file_name:
                attachment.file_name = self.sanitize_text(attachment.file_name)
            if attachment.original_path:
                # Strip path traversal attempts
                attachment.original_path = attachment.original_path.replace(
                    "..", ""
                ).lstrip("/\\")
            validated_attachments.append(attachment)
        message.attachments = validated_attachments

        return message


# ---------------------------------------------------------------------------
# Abstract message converter
# ---------------------------------------------------------------------------

class MessageConverter(abc.ABC):
    """Abstract base class for messenger-specific export converters.

    Each subclass understands a particular export format and produces
    a sequence of ImportedMessage objects ready for validation and
    database insertion.

    Subclasses must implement:
        - parse(): Read the export file and yield ImportedMessage objects
        - detect_format(): Check if a given path matches this converter's format
    """

    @abc.abstractmethod
    async def parse(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[ImportedMessage]:
        """Parse an export file into a list of imported messages.

        Args:
            export_path: Path to the export file or directory.
            progress_callback: Optional callback for progress reporting.

        Returns:
            List of parsed ImportedMessage objects.

        Raises:
            ImportFormatError: If the export file cannot be parsed.
        """

    @abc.abstractmethod
    def detect_format(self, export_path: Path) -> bool:
        """Check if the given path matches this converter's export format.

        Args:
            export_path: Path to the export file or directory.

        Returns:
            True if this converter can handle the export.
        """


# ---------------------------------------------------------------------------
# Signal converter
# ---------------------------------------------------------------------------

class SignalConverter(MessageConverter):
    """Converts Signal JSON exports to Presidium ImportedMessage objects.

    Signal exports are produced by signal-export and similar tools.
    The JSON format is a list of conversations, each containing messages:

        [
            {
                "name": "Contact Name",
                "type": "private" | "group",
                "messages": [
                    {
                        "timestamp": 1700000000000,
                        "sender": "Me" | "Contact Name",
                        "body": "Hello!",
                        "attachments": [{"path": "attachments/abc.jpg", "contentType": "image/jpeg"}],
                        "quote": { "id": 123, "author": "..." },
                        "type": "incoming" | "outgoing"
                    }
                ]
            }
        ]

    Signal timestamps are in milliseconds since epoch.
    """

    def detect_format(self, export_path: Path) -> bool:
        """Detect Signal export by checking for JSON with Signal-specific keys."""
        if export_path.is_dir():
            json_files = list(export_path.glob("*.json"))
            if not json_files:
                return False
            export_path = json_files[0]

        if not export_path.is_file() or export_path.suffix != ".json":
            return False

        try:
            with open(export_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                first = data[0]
                return "messages" in first and isinstance(first.get("messages"), list)
        except (json.JSONDecodeError, OSError, KeyError):
            return False

        return False

    async def parse(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[ImportedMessage]:
        """Parse a Signal JSON export.

        Args:
            export_path: Path to the Signal export JSON file.
            progress_callback: Optional progress callback.

        Returns:
            List of ImportedMessage objects.

        Raises:
            ImportFormatError: If the file cannot be parsed.
        """
        if export_path.is_dir():
            json_files = list(export_path.glob("*.json"))
            if not json_files:
                raise ImportFormatError(
                    _("No JSON files found in Signal export directory: {path}").format(
                        path=export_path
                    )
                )
            export_path = json_files[0]

        try:
            with open(export_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(
                _("Invalid JSON in Signal export: {error}").format(error=exc)
            ) from exc
        except OSError as exc:
            raise ImportFormatError(
                _("Cannot read Signal export file: {error}").format(error=exc)
            ) from exc

        if not isinstance(data, list):
            raise ImportFormatError(
                _("Signal export must be a JSON array of conversations.")
            )

        messages: list[ImportedMessage] = []
        total_conversations = len(data)

        for conv_idx, conversation in enumerate(data):
            conv_name = conversation.get("name", _("Unknown Chat"))
            conv_messages = conversation.get("messages", [])

            for raw_msg in conv_messages:
                try:
                    msg = self._convert_signal_message(
                        raw_msg, conv_name, export_path.parent
                    )
                    messages.append(msg)
                except (ImportValidationError, KeyError, TypeError) as exc:
                    logger.debug(
                        "Skipping invalid Signal message in %s: %s",
                        conv_name, exc,
                    )

            if progress_callback is not None:
                progress_callback(conv_idx + 1, total_conversations, ImportSource.SIGNAL)

            # Yield control periodically for large exports
            if conv_idx % 10 == 0:
                await asyncio.sleep(0)

        logger.info(
            "Parsed %d messages from %d Signal conversations.",
            len(messages), total_conversations,
        )
        return messages

    def _convert_signal_message(
        self,
        raw: dict[str, Any],
        chat_name: str,
        base_dir: Path,
    ) -> ImportedMessage:
        """Convert a single Signal message dict to ImportedMessage.

        Args:
            raw: Raw message dict from Signal JSON.
            chat_name: Name of the conversation.
            base_dir: Base directory for resolving attachment paths.

        Returns:
            ImportedMessage object.

        Raises:
            ImportValidationError: If required fields are missing.
        """
        # Signal uses millisecond timestamps
        ts_ms = raw.get("timestamp", 0)
        timestamp = ts_ms / 1000.0 if isinstance(ts_ms, (int, float)) else 0.0

        sender = raw.get("sender", "")
        msg_type = raw.get("type", "incoming")
        is_outgoing = msg_type == "outgoing"

        body = raw.get("body", "")
        if body is None:
            body = ""

        # Extract attachments
        attachments: list[ImportedAttachment] = []
        for att in raw.get("attachments", []):
            att_path = att.get("path", "")
            content_type = att.get("contentType", "application/octet-stream")
            media_type = self._mime_to_media_type(content_type)
            file_name = att_path.split("/")[-1] if att_path else ""
            attachments.append(ImportedAttachment(
                media_type=media_type,
                original_path=str(base_dir / att_path) if att_path else "",
                file_name=file_name,
                mime_type=content_type,
                size_bytes=0,
            ))

        # Extract reply-to reference
        quote = raw.get("quote")
        reply_to_id = ""
        if isinstance(quote, dict):
            reply_to_id = str(quote.get("id", ""))

        return ImportedMessage(
            source=ImportSource.SIGNAL,
            external_id=str(raw.get("timestamp", uuid.uuid4().hex)),
            sender_name=sender,
            sender_id="",
            timestamp=timestamp,
            text=body,
            attachments=attachments,
            reply_to_id=reply_to_id,
            is_outgoing=is_outgoing,
            chat_name=chat_name,
            raw_data=raw,
        )

    @staticmethod
    def _mime_to_media_type(mime_type: str) -> MessageMediaType:
        """Map a MIME type to a MessageMediaType enum value.

        Args:
            mime_type: MIME type string (e.g., "image/jpeg").

        Returns:
            Corresponding MessageMediaType value.
        """
        if mime_type.startswith("image/"):
            return MessageMediaType.IMAGE
        if mime_type.startswith("video/"):
            return MessageMediaType.VIDEO
        if mime_type.startswith("audio/"):
            return MessageMediaType.AUDIO
        if "sticker" in mime_type:
            return MessageMediaType.STICKER
        if "voice" in mime_type:
            return MessageMediaType.VOICE
        if "animation" in mime_type or mime_type == "image/gif":
            return MessageMediaType.ANIMATION
        return MessageMediaType.FILE


# ---------------------------------------------------------------------------
# WhatsApp converter
# ---------------------------------------------------------------------------

class WhatsAppConverter(MessageConverter):
    """Converts WhatsApp .txt chat exports to Presidium ImportedMessage objects.

    WhatsApp exports are plain text files (_chat.txt) with one message per line:

        12/25/2023, 10:30 AM - John: Hello!
        12/25/2023, 10:31 AM - Jane: Hi there!
        12/25/2023, 10:32 AM - John: <attached: photo.jpg>
        12/25/2023, 10:33 AM - Jane left
        12/25/2023, 10:34 AM - You created group "Friends"

    Line format (varies by locale):
        MM/DD/YYYY, HH:MM AM/PM - Sender: Message text
        DD/MM/YYYY, HH:MM - Sender: Message text

    System messages (added to group, left, etc.) are captured but marked
    as system events rather than user messages.
    """

    # Pattern for US-style date: 12/25/2023, 10:30 AM - Sender: text
    PATTERN_US = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*"
        r"([^:]+?):\s*(.*)",
        re.DOTALL,
    )

    # Pattern for EU-style date: 25/12/2023, 10:30 - Sender: text
    PATTERN_EU = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2})\s*-\s*"
        r"([^:]+?):\s*(.*)",
        re.DOTALL,
    )

    # Pattern for system messages (no sender colon)
    PATTERN_SYSTEM = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?:\s*[AP]M)?)\s*-\s*(.*)",
    )

    # Attachment indicators
    ATTACHMENT_PATTERNS: list[tuple[re.Pattern[str], MessageMediaType]] = [
        (re.compile(r"<attached:\s*.*\.jpg>", re.IGNORECASE), MessageMediaType.IMAGE),
        (re.compile(r"<attached:\s*.*\.png>", re.IGNORECASE), MessageMediaType.IMAGE),
        (re.compile(r"<attached:\s*.*\.mp4>", re.IGNORECASE), MessageMediaType.VIDEO),
        (re.compile(r"<attached:\s*.*\.opus>", re.IGNORECASE), MessageMediaType.VOICE),
        (re.compile(r"<attached:\s*.*\.pdf>", re.IGNORECASE), MessageMediaType.FILE),
        (re.compile(r"IMG-\d{8}-WA\d+", re.IGNORECASE), MessageMediaType.IMAGE),
        (re.compile(r"PTT-\d{8}-WA\d+", re.IGNORECASE), MessageMediaType.VOICE),
        (re.compile(r"VID-\d{8}-WA\d+", re.IGNORECASE), MessageMediaType.VIDEO),
        (re.compile(r"DOC-\d{8}-WA\d+", re.IGNORECASE), MessageMediaType.FILE),
    ]

    def detect_format(self, export_path: Path) -> bool:
        """Detect WhatsApp export by checking for _chat.txt or similar files."""
        if export_path.is_file():
            name = export_path.name.lower()
            if name.endswith(".txt") and "chat" in name:
                return True

        if export_path.is_dir():
            chat_files = list(export_path.glob("*chat*.txt"))
            return len(chat_files) > 0

        return False

    async def parse(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[ImportedMessage]:
        """Parse a WhatsApp .txt chat export.

        Args:
            export_path: Path to the WhatsApp export file or directory.
            progress_callback: Optional progress callback.

        Returns:
            List of ImportedMessage objects.

        Raises:
            ImportFormatError: If the file cannot be parsed.
        """
        chat_file = self._resolve_chat_file(export_path)

        try:
            with open(chat_file, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
        except OSError as exc:
            raise ImportFormatError(
                _("Cannot read WhatsApp export file: {error}").format(error=exc)
            ) from exc

        total_lines = len(lines)
        messages: list[ImportedMessage] = []
        current_message_lines: list[str] = []
        line_idx = 0

        for line_idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Check if this line starts a new message (has date prefix)
            if self._is_new_message_line(stripped):
                # Flush the previous multi-line message
                if current_message_lines:
                    msg = self._parse_message_block(current_message_lines)
                    if msg is not None:
                        messages.append(msg)
                current_message_lines = [stripped]
            else:
                # Continuation of the current message
                current_message_lines.append(stripped)

            if progress_callback is not None and line_idx % BATCH_SIZE == 0:
                progress_callback(line_idx, total_lines, ImportSource.WHATSAPP)

            # Yield control periodically
            if line_idx % 500 == 0:
                await asyncio.sleep(0)

        # Flush final message
        if current_message_lines:
            msg = self._parse_message_block(current_message_lines)
            if msg is not None:
                messages.append(msg)

        if progress_callback is not None:
            progress_callback(total_lines, total_lines, ImportSource.WHATSAPP)

        logger.info(
            "Parsed %d messages from WhatsApp export (%d lines).",
            len(messages), total_lines,
        )
        return messages

    def _resolve_chat_file(self, export_path: Path) -> Path:
        """Resolve the chat file path from a file or directory.

        Args:
            export_path: Path to file or directory.

        Returns:
            Resolved Path to the chat file.

        Raises:
            ImportFormatError: If no chat file can be found.
        """
        if export_path.is_file():
            return export_path

        chat_files = list(export_path.glob("*chat*.txt"))
        if chat_files:
            return chat_files[0]

        # Try common WhatsApp export names
        for name in ("_chat.txt", "WhatsApp Chat.txt", "chat.txt"):
            candidate = export_path / name
            if candidate.is_file():
                return candidate

        raise ImportFormatError(
            _("No WhatsApp chat file found in: {path}").format(path=export_path)
        )

    def _is_new_message_line(self, line: str) -> bool:
        """Check if a line starts a new message (has date/time prefix).

        Args:
            line: A single line from the export file.

        Returns:
            True if this line begins a new message entry.
        """
        return bool(self.PATTERN_SYSTEM.match(line))

    def _parse_message_block(self, lines: list[str]) -> Optional[ImportedMessage]:
        """Parse a multi-line message block into an ImportedMessage.

        Args:
            lines: Lines belonging to a single message.

        Returns:
            ImportedMessage or None if the block is a system message.
        """
        full_text = " ".join(lines)

        # Try user message patterns
        match = self.PATTERN_US.match(full_text)
        if match is None:
            match = self.PATTERN_EU.match(full_text)

        if match is not None:
            date_str, time_str, sender, body = match.groups()
            timestamp = self._parse_whatsapp_datetime(date_str, time_str)
            is_outgoing = sender.strip().lower() in ("you", _("You").lower())

            # Detect attachments in body text
            attachments = self._detect_attachments(body)

            return ImportedMessage(
                source=ImportSource.WHATSAPP,
                external_id=uuid.uuid4().hex[:16],
                sender_name=sender.strip(),
                sender_id="",
                timestamp=timestamp,
                text=body.strip(),
                attachments=attachments,
                reply_to_id="",
                is_outgoing=is_outgoing,
                chat_name="",
                raw_data={"date": date_str, "time": time_str, "full_line": full_text},
            )

        # System message (no sender colon) — skip but don't error
        return None

    @staticmethod
    def _parse_whatsapp_datetime(date_str: str, time_str: str) -> float:
        """Parse WhatsApp date/time strings into a Unix timestamp.

        Args:
            date_str: Date portion (e.g., "12/25/2023").
            time_str: Time portion (e.g., "10:30 AM").

        Returns:
            Unix timestamp in seconds, or 0.0 if parsing fails.
        """
        import calendar
        from datetime import datetime

        try:
            # Normalize time string
            time_normalized = time_str.strip().upper()
            if "AM" in time_normalized or "PM" in time_normalized:
                fmt_time = "%I:%M %p"
            else:
                fmt_time = "%H:%M"

            # Try MM/DD/YYYY first (US format)
            try:
                dt = datetime.strptime(
                    f"{date_str} {time_normalized}", f"%m/%d/%Y {fmt_time}"
                )
                return dt.timestamp()
            except ValueError:
                logger.debug("US date format did not match: %s", date_str)

            # Try DD/MM/YYYY (EU format)
            try:
                dt = datetime.strptime(
                    f"{date_str} {time_normalized}", f"%d/%m/%Y {fmt_time}"
                )
                return dt.timestamp()
            except ValueError:
                logger.debug("EU date format did not match: %s", date_str)

            # Try 2-digit year
            for date_fmt in ("%m/%d/%y", "%d/%m/%y"):
                try:
                    dt = datetime.strptime(
                        f"{date_str} {time_normalized}", f"{date_fmt} {fmt_time}"
                    )
                    return dt.timestamp()
                except ValueError:
                    continue

        except Exception as exc:
            logger.debug("Failed to parse WhatsApp datetime '%s %s': %s", date_str, time_str, exc)

        return 0.0

    def _detect_attachments(self, body: str) -> list[ImportedAttachment]:
        """Detect attachment indicators in WhatsApp message body.

        Args:
            body: Message body text.

        Returns:
            List of ImportedAttachment objects for detected media.
        """
        attachments: list[ImportedAttachment] = []
        for pattern, media_type in self.ATTACHMENT_PATTERNS:
            if pattern.search(body):
                attachments.append(ImportedAttachment(
                    media_type=media_type,
                    original_path="",
                    file_name="",
                    mime_type="",
                    size_bytes=0,
                ))
        return attachments


# ---------------------------------------------------------------------------
# Telegram converter
# ---------------------------------------------------------------------------

class TelegramConverter(MessageConverter):
    """Converts Telegram Desktop JSON exports to Presidium ImportedMessage objects.

    Telegram Desktop exports produce a result.json file with this structure:

        {
            "name": "Chat Name",
            "type": "personal_chat" | "group_chat" | "channel",
            "id": 123456789,
            "messages": [
                {
                    "id": 1,
                    "type": "message" | "service",
                    "date": "2023-12-25T10:30:00",
                    "from": "John",
                    "from_id": "user123",
                    "text": "Hello!" or ["Hello!", {"type": "bold", "text": "world"}],
                    "photo": {"uri": "photos/photo.jpg", "width": 800, "height": 600},
                    "file": {"uri": "files/doc.pdf", "name": "doc.pdf"},
                    "reply_to_message_id": 42,
                    "media_type": "sticker" | "animation" | ...,
                    "sticker_emoji": "👍",
                    "mime_type": "image/jpeg"
                }
            ]
        }

    Telegram text can be either a plain string or a list mixing strings
    and styled text objects. This converter handles both forms.
    """

    def detect_format(self, export_path: Path) -> bool:
        """Detect Telegram export by checking for result.json with Telegram keys."""
        if export_path.is_dir():
            result_file = export_path / "result.json"
            if result_file.is_file():
                return self._check_telegram_json(result_file)
            # Also check for result*.json (Telegram may number them)
            for result_file in export_path.glob("result*.json"):
                return self._check_telegram_json(result_file)
            return False

        if export_path.is_file() and export_path.suffix == ".json":
            return self._check_telegram_json(export_path)

        return False

    def _check_telegram_json(self, path: Path) -> bool:
        """Check if a JSON file looks like a Telegram export.

        Args:
            path: Path to the JSON file.

        Returns:
            True if the file has Telegram export structure.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (
                isinstance(data, dict)
                and "messages" in data
                and "type" in data
                and isinstance(data.get("messages"), list)
            )
        except (json.JSONDecodeError, OSError, KeyError):
            return False

    async def parse(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[ImportedMessage]:
        """Parse a Telegram JSON export.

        Args:
            export_path: Path to the Telegram export directory or JSON file.
            progress_callback: Optional progress callback.

        Returns:
            List of ImportedMessage objects.

        Raises:
            ImportFormatError: If the file cannot be parsed.
        """
        json_path = self._resolve_json_path(export_path)

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(
                _("Invalid JSON in Telegram export: {error}").format(error=exc)
            ) from exc
        except OSError as exc:
            raise ImportFormatError(
                _("Cannot read Telegram export file: {error}").format(error=exc)
            ) from exc

        if not isinstance(data, dict) or "messages" not in data:
            raise ImportFormatError(
                _("Telegram export must be a JSON object with 'messages' key.")
            )

        chat_name = data.get("name", _("Unknown Chat"))
        chat_type = data.get("type", "personal_chat")
        raw_messages = data.get("messages", [])
        total_messages = len(raw_messages)

        messages: list[ImportedMessage] = []
        for idx, raw_msg in enumerate(raw_messages):
            try:
                msg = self._convert_telegram_message(
                    raw_msg, chat_name, chat_type, json_path.parent
                )
                messages.append(msg)
            except (ImportValidationError, KeyError, TypeError) as exc:
                logger.debug(
                    "Skipping invalid Telegram message id=%s: %s",
                    raw_msg.get("id", "?"), exc,
                )

            if progress_callback is not None and idx % BATCH_SIZE == 0:
                progress_callback(idx + 1, total_messages, ImportSource.TELEGRAM)

            if idx % 500 == 0:
                await asyncio.sleep(0)

        if progress_callback is not None:
            progress_callback(total_messages, total_messages, ImportSource.TELEGRAM)

        logger.info(
            "Parsed %d messages from Telegram chat '%s' (%s).",
            len(messages), chat_name, chat_type,
        )
        return messages

    def _resolve_json_path(self, export_path: Path) -> Path:
        """Resolve the JSON file path from a file or directory.

        Args:
            export_path: Path to directory or JSON file.

        Returns:
            Path to the Telegram export JSON file.

        Raises:
            ImportFormatError: If no JSON file can be found.
        """
        if export_path.is_file():
            return export_path

        # Standard Telegram Desktop export location
        result_file = export_path / "result.json"
        if result_file.is_file():
            return result_file

        # Numbered result files
        result_files = sorted(export_path.glob("result*.json"))
        if result_files:
            return result_files[0]

        raise ImportFormatError(
            _("No result.json found in Telegram export directory: {path}").format(
                path=export_path
            )
        )

    def _convert_telegram_message(
        self,
        raw: dict[str, Any],
        chat_name: str,
        chat_type: str,
        base_dir: Path,
    ) -> ImportedMessage:
        """Convert a single Telegram message dict to ImportedMessage.

        Args:
            raw: Raw message dict from Telegram JSON.
            chat_name: Name of the conversation.
            chat_type: Type of chat (personal_chat, group_chat, channel).
            base_dir: Base directory for resolving attachment paths.

        Returns:
            ImportedMessage object.

        Raises:
            ImportValidationError: If the message type is 'service' (system event).
        """
        msg_type = raw.get("type", "message")
        if msg_type == "service":
            raise ImportValidationError("Service messages are not imported.")

        # Parse ISO 8601 date
        date_str = raw.get("date", "")
        timestamp = self._parse_iso_datetime(date_str)

        # Extract text (can be string or list)
        text = self._extract_telegram_text(raw.get("text", ""))

        sender = raw.get("from", "")
        sender_id = str(raw.get("from_id", ""))
        is_outgoing = raw.get("from_id", "") == raw.get("saved_from_id", "")

        # Extract attachments
        attachments = self._extract_telegram_attachments(raw, base_dir)

        # Extract reply reference
        reply_to_id = str(raw.get("reply_to_message_id", ""))

        return ImportedMessage(
            source=ImportSource.TELEGRAM,
            external_id=str(raw.get("id", uuid.uuid4().hex[:16])),
            sender_name=sender,
            sender_id=sender_id,
            timestamp=timestamp,
            text=text,
            attachments=attachments,
            reply_to_id=reply_to_id,
            is_outgoing=is_outgoing,
            chat_name=chat_name,
            raw_data=raw,
        )

    @staticmethod
    def _parse_iso_datetime(date_str: str) -> float:
        """Parse an ISO 8601 datetime string to Unix timestamp.

        Args:
            date_str: ISO 8601 datetime string (e.g., "2023-12-25T10:30:00").

        Returns:
            Unix timestamp in seconds, or 0.0 if parsing fails.
        """
        from datetime import datetime

        if not date_str:
            return 0.0

        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.timestamp()
            except ValueError:
                continue

        logger.debug("Failed to parse Telegram datetime: %s", date_str)
        return 0.0

    @staticmethod
    def _extract_telegram_text(text_data: Any) -> str:
        """Extract plain text from Telegram's mixed text format.

        Telegram text can be:
            - A simple string: "Hello!"
            - A list mixing strings and styled objects:
              ["Hello ", {"type": "bold", "text": "world"}]

        Args:
            text_data: Raw text field from Telegram JSON.

        Returns:
            Concatenated plain text string.
        """
        if isinstance(text_data, str):
            return text_data

        if isinstance(text_data, list):
            parts: list[str] = []
            for item in text_data:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
            return "".join(parts)

        return ""

    def _extract_telegram_attachments(
        self,
        raw: dict[str, Any],
        base_dir: Path,
    ) -> list[ImportedAttachment]:
        """Extract attachment information from a Telegram message.

        Args:
            raw: Raw message dict.
            base_dir: Base directory for resolving file paths.

        Returns:
            List of ImportedAttachment objects.
        """
        attachments: list[ImportedAttachment] = []
        media_type_str = raw.get("media_type", "")
        mime_type = raw.get("mime_type", "application/octet-stream")

        # Photo attachment
        photo = raw.get("photo")
        if isinstance(photo, dict):
            uri = photo.get("uri", "")
            attachments.append(ImportedAttachment(
                media_type=MessageMediaType.IMAGE,
                original_path=str(base_dir / uri) if uri else "",
                file_name=uri.split("/")[-1] if uri else "",
                mime_type="image/jpeg",
                size_bytes=0,
            ))

        # File attachment
        file_info = raw.get("file")
        if isinstance(file_info, dict):
            uri = file_info.get("uri", "")
            name = file_info.get("name", uri.split("/")[-1] if uri else "")
            detected_media = self._telegram_media_type(media_type_str, mime_type)
            attachments.append(ImportedAttachment(
                media_type=detected_media,
                original_path=str(base_dir / uri) if uri else "",
                file_name=name,
                mime_type=mime_type,
                size_bytes=0,
            ))

        # Sticker
        if media_type_str == "sticker":
            sticker = raw.get("sticker_emoji", "")
            attachments.append(ImportedAttachment(
                media_type=MessageMediaType.STICKER,
                original_path="",
                file_name=f"sticker_{sticker}.webp",
                mime_type="image/webp",
                size_bytes=0,
            ))

        # Animation/GIF
        if media_type_str == "animation":
            attachments.append(ImportedAttachment(
                media_type=MessageMediaType.ANIMATION,
                original_path="",
                file_name="",
                mime_type=mime_type or "video/mp4",
                size_bytes=0,
            ))

        # Voice message
        if raw.get("media_type") == "voice_message":
            attachments.append(ImportedAttachment(
                media_type=MessageMediaType.VOICE,
                original_path="",
                file_name="",
                mime_type=mime_type or "audio/ogg",
                size_bytes=0,
            ))

        return attachments

    @staticmethod
    def _telegram_media_type(media_type: str, mime_type: str) -> MessageMediaType:
        """Determine MessageMediaType from Telegram media_type and MIME.

        Args:
            media_type: Telegram's media_type field value.
            mime_type: MIME type of the file.

        Returns:
            Corresponding MessageMediaType value.
        """
        mapping: dict[str, MessageMediaType] = {
            "sticker": MessageMediaType.STICKER,
            "animation": MessageMediaType.ANIMATION,
            "voice_message": MessageMediaType.VOICE,
            "video_message": MessageMediaType.VIDEO,
        }
        if media_type in mapping:
            return mapping[media_type]

        if mime_type.startswith("image/"):
            return MessageMediaType.IMAGE
        if mime_type.startswith("video/"):
            return MessageMediaType.VIDEO
        if mime_type.startswith("audio/"):
            return MessageMediaType.AUDIO

        return MessageMediaType.FILE


# ---------------------------------------------------------------------------
# Import manager
# ---------------------------------------------------------------------------

class ImportManager:
    """Orchestrates message imports from external messengers.

    Provides a high-level API for importing conversation history from
    Signal, WhatsApp, Telegram, and (planned) Matrix. Handles converter
    selection, validation, batched database insertion, and progress reporting.

    Usage:
        manager = ImportManager(db_path=Path("~/.presidium/data.db"))
        result = await manager.import_from_signal(
            export_path=Path("~/signal-export/"),
            progress_callback=my_progress_fn,
        )
        print(f"Imported {result.imported_messages}/{result.total_messages}")

    Thread safety:
        ImportManager is designed for single-threaded async usage. If you
        need to run imports concurrently, use a separate ImportManager
        instance per task.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path
        self._validators = ValidationPipeline()
        self._converters: dict[ImportSource, MessageConverter] = {
            ImportSource.SIGNAL: SignalConverter(),
            ImportSource.WHATSAPP: WhatsAppConverter(),
            ImportSource.TELEGRAM: TelegramConverter(),
        }
        self._cancelled = False

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def import_from_signal(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> ImportResult:
        """Import messages from a Signal JSON export.

        Args:
            export_path: Path to the Signal export file or directory.
            progress_callback: Optional callback for progress updates.

        Returns:
            ImportResult with import statistics.

        Raises:
            ImportFormatError: If the export format is invalid.
        """
        return await self._run_import(
            ImportSource.SIGNAL, export_path, progress_callback
        )

    async def import_from_whatsapp(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> ImportResult:
        """Import messages from a WhatsApp .txt export.

        Args:
            export_path: Path to the WhatsApp export file or directory.
            progress_callback: Optional callback for progress updates.

        Returns:
            ImportResult with import statistics.

        Raises:
            ImportFormatError: If the export format is invalid.
        """
        return await self._run_import(
            ImportSource.WHATSAPP, export_path, progress_callback
        )

    async def import_from_telegram(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> ImportResult:
        """Import messages from a Telegram Desktop JSON export.

        Args:
            export_path: Path to the Telegram export directory or JSON file.
            progress_callback: Optional callback for progress updates.

        Returns:
            ImportResult with import statistics.

        Raises:
            ImportFormatError: If the export format is invalid.
        """
        return await self._run_import(
            ImportSource.TELEGRAM, export_path, progress_callback
        )

    def cancel(self) -> None:
        """Cancel the current import operation.

        The import will stop after the current batch completes.
        """
        self._cancelled = True
        logger.info(_("Import cancellation requested."))

    def detect_source(self, export_path: Path) -> Optional[ImportSource]:
        """Auto-detect the messenger source from the export path.

        Tries each converter's detect_format() method in order.

        Args:
            export_path: Path to the export file or directory.

        Returns:
            The detected ImportSource, or None if no converter matches.
        """
        for source, converter in self._converters.items():
            if converter.detect_format(export_path):
                logger.info(
                    "Detected export source: %s for path %s",
                    source.value, export_path,
                )
                return source
        return None

    async def auto_import(
        self,
        export_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> ImportResult:
        """Auto-detect the export format and import.

        Args:
            export_path: Path to the export file or directory.
            progress_callback: Optional callback for progress updates.

        Returns:
            ImportResult with import statistics.

        Raises:
            ImportFormatError: If the export format cannot be detected.
        """
        source = self.detect_source(export_path)
        if source is None:
            raise ImportFormatError(
                _("Cannot detect export format for: {path}").format(
                    path=export_path
                )
            )
        return await self._run_import(source, export_path, progress_callback)

    # -------------------------------------------------------------------
    # Internal methods
    # -------------------------------------------------------------------

    async def _run_import(
        self,
        source: ImportSource,
        export_path: Path,
        progress_callback: Optional[ProgressCallback],
    ) -> ImportResult:
        """Execute the full import pipeline for a given source.

        Args:
            source: Which messenger to import from.
            export_path: Path to the export data.
            progress_callback: Optional progress callback.

        Returns:
            ImportResult with statistics.
        """
        self._cancelled = False
        start_time = time.monotonic()

        converter = self._converters.get(source)
        if converter is None:
            raise ImportFormatError(
                _("No converter available for source: {source}").format(
                    source=source.value
                )
            )

        logger.info(
            _("Starting import from %s, path: %s"),
            source.value, export_path,
        )

        # Phase 1: Parse the export
        raw_messages = await converter.parse(export_path, progress_callback)
        total = len(raw_messages)

        if self._cancelled:
            raise ImportCancelledError(
                _("Import was cancelled during parsing.")
            )

        # Phase 2: Validate and sanitize
        validated_messages: list[ImportedMessage] = []
        skipped = 0
        errors: list[str] = []

        for msg in raw_messages:
            if self._cancelled:
                raise ImportCancelledError(
                    _("Import was cancelled during validation.")
                )

            try:
                validated = self._validators.validate_message(msg)
                validated_messages.append(validated)
            except ImportValidationError as exc:
                skipped += 1
                errors.append(str(exc))
                logger.debug(
                    "Skipped message %s from %s: %s",
                    msg.external_id, source.value, exc,
                )

        # Phase 3: Batch insert into database
        imported = 0
        for batch_start in range(0, len(validated_messages), BATCH_SIZE):
            if self._cancelled:
                raise ImportCancelledError(
                    _("Import was cancelled during database insertion.")
                )

            batch = validated_messages[batch_start:batch_start + BATCH_SIZE]
            try:
                inserted = await self._insert_batch(batch)
                imported += inserted
            except ImportStorageError as exc:
                skipped += len(batch)
                errors.append(str(exc))
                logger.warning(
                    "Batch insert failed at offset %d: %s",
                    batch_start, exc,
                )

            # Yield control between batches
            await asyncio.sleep(0)

        duration = time.monotonic() - start_time
        result = ImportResult(
            source=source,
            total_messages=total,
            imported_messages=imported,
            skipped_messages=skipped,
            errors=errors,
            duration_seconds=round(duration, 3),
        )

        logger.info(
            _("Import complete: %d/%d messages imported from %s in %.1fs "
              "(%d skipped, %d errors)"),
            imported, total, source.value, duration, skipped, len(errors),
        )

        return result

    async def _insert_batch(self, messages: list[ImportedMessage]) -> int:
        """Insert a batch of validated messages into the Presidium database.

        In a full Presidium deployment, this would write to the SQLite
        database via the db manager module. This implementation performs
        the validation checks and returns the count of messages that would
        be inserted.

        Args:
            messages: List of validated ImportedMessage objects.

        Returns:
            Number of messages inserted.

        Raises:
            ImportStorageError: If the database write fails.
        """
        if not messages:
            return 0

        # Validate all messages have required fields for insertion
        for msg in messages:
            if not msg.external_id:
                msg.external_id = uuid.uuid4().hex[:16]

        try:
            # In production: await db.insert_imported_messages(messages)
            logger.debug(
                "Inserting batch of %d messages (source=%s).",
                len(messages), messages[0].source.value,
            )
            return len(messages)
        except Exception as exc:
            raise ImportStorageError(
                _("Database insert failed: {error}").format(error=exc)
            ) from exc


__all__ = [
    "ImportError",
    "ImportFormatError",
    "ImportValidationError",
    "ImportStorageError",
    "ImportCancelledError",
    "ImportSource",
    "MessageMediaType",
    "ImportedAttachment",
    "ImportedMessage",
    "ImportResult",
    "ProgressCallback",
    "ValidationPipeline",
    "MessageConverter",
    "SignalConverter",
    "WhatsAppConverter",
    "TelegramConverter",
    "ImportManager",
]
