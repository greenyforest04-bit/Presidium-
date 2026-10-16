"""Database manager for Presidium.

Provides async access to the SQLite database via aiosqlite:
- Contact management (add, remove, query)
- Message persistence and retrieval
- File transfer tracking
- Settings storage
- DHT routing table persistence
"""

from __future__ import annotations

import logging
import base64
from pathlib import Path
from typing import Optional

import aiosqlite
from presidium.core.crypto import AES256GCM, SecureBytes

from presidium.core.messaging.chat import ChatMessage, MessageStatus
from presidium.db.models import ALL_CREATE_STATEMENTS, SCHEMA_VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class DatabaseError(Exception):
    """Base database error."""


class DatabaseNotInitializedError(DatabaseError):
    """Database connection not established."""


class RecordNotFoundError(DatabaseError):
    """Requested record not found."""


# ---------------------------------------------------------------------------
# Contact data class
# ---------------------------------------------------------------------------


class ContactRecord:
    """Represents a contact record from the database."""

    def __init__(
        self,
        user_id: str,
        public_key: bytes,
        nickname: str = "",
        added_at: float = 0.0,
        last_seen: float = 0.0,
        is_blocked: bool = False,
    ) -> None:
        self.user_id = user_id
        self.public_key = public_key
        self.nickname = nickname
        self.added_at = added_at
        self.last_seen = last_seen
        self.is_blocked = is_blocked


# ---------------------------------------------------------------------------
# Database Manager
# ---------------------------------------------------------------------------


class DatabaseManager:
    """Async SQLite database manager.

    Usage:
        db = DatabaseManager(db_path)
        await db.initialize()
        # ... use db methods ...
        await db.close()
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: Optional[aiosqlite.Connection] = None
        self._history_key: SecureBytes | None = None

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the database connection and create tables if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute("PRAGMA secure_delete = ON") as cursor:
            await cursor.fetchall()

        # Create all tables
        for stmt in ALL_CREATE_STATEMENTS:
            await self._db.execute(stmt)

        # Set schema version
        await self._db.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        await self._db.commit()
        logger.info("Database initialized at %s (schema v%d)", self._db_path, SCHEMA_VERSION)

    async def close(self) -> None:
        """Close the database connection."""
        self.lock_history()
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database connection closed.")

    def lock_history(self) -> None:
        if self._history_key is not None:
            self._history_key.zero()
            self._history_key = None

    async def unlock_history(self, key: bytes | SecureBytes) -> None:
        """Unlock history and migrate legacy plaintext without retaining a search index."""
        db = self._require_db()
        candidate = SecureBytes(bytes(key))
        try:
            async with db.execute(
                "SELECT value FROM metadata WHERE key = 'history_key_check'"
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                if (
                    AES256GCM.decrypt(
                        candidate, base64.b64decode(row[0]), b"presidium-history-check-v1"
                    )
                    != b"history"
                ):
                    raise DatabaseError("Incorrect history key")
            else:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    for trigger in (
                        "messages_fts_insert",
                        "messages_fts_delete",
                        "messages_fts_update",
                    ):
                        await db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                    await db.execute("DROP TABLE IF EXISTS messages_fts")
                    async with db.execute(
                        "SELECT message_id, sender_user_id, recipient_user_id, content FROM messages"
                    ) as cursor:
                        rows = await cursor.fetchall()
                    for message in rows:
                        aad = self._message_aad(message[0], message[1], message[2])
                        encrypted = base64.b64encode(
                            AES256GCM.encrypt(candidate, message[3].encode(), aad)
                        ).decode()
                        await db.execute(
                            "UPDATE messages SET content = ? WHERE message_id = ?",
                            ("enc:v1:" + encrypted, message[0]),
                        )
                    check = base64.b64encode(
                        AES256GCM.encrypt(candidate, b"history", b"presidium-history-check-v1")
                    ).decode()
                    await db.execute(
                        "INSERT INTO metadata(key, value) VALUES ('history_key_check', ?)", (check,)
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
                async with db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
                    await cursor.fetchall()
                await db.execute("VACUUM")
            self.lock_history()
            self._history_key = candidate
        except Exception:
            candidate.zero()
            raise

    @staticmethod
    def _message_aad(message_id: str, sender: str, recipient: str) -> bytes:
        import json

        return json.dumps(
            ["presidium-message-v1", message_id, sender, recipient], separators=(",", ":")
        ).encode()

    def _encrypt_content(self, message: ChatMessage) -> str:
        if self._history_key is None:
            raise DatabaseError("History is locked")
        aad = self._message_aad(
            message.message_id_hex, message.sender_user_id, message.recipient_user_id
        )
        return (
            "enc:v1:"
            + base64.b64encode(
                AES256GCM.encrypt(self._history_key, message.content.encode(), aad)
            ).decode()
        )

    def _decrypt_content(self, row) -> str:
        if self._history_key is None:
            raise DatabaseError("History is locked")
        if not row["content"].startswith("enc:v1:"):
            raise DatabaseError("Unencrypted history record requires migration")
        aad = self._message_aad(row["message_id"], row["sender_user_id"], row["recipient_user_id"])
        return AES256GCM.decrypt(
            self._history_key, base64.b64decode(row["content"][7:], validate=True), aad
        ).decode()

    def _require_db(self) -> aiosqlite.Connection:
        """Get the database connection or raise if not initialized."""
        if self._db is None:
            raise DatabaseNotInitializedError("Database not initialized. Call initialize() first.")
        return self._db

    # -------------------------------------------------------------------
    # Contacts
    # -------------------------------------------------------------------

    async def add_contact(
        self,
        user_id: str,
        public_key: bytes,
        nickname: str = "",
    ) -> None:
        """Add or update a contact."""
        import time

        db = self._require_db()
        await db.execute(
            """INSERT OR REPLACE INTO contacts
               (user_id, public_key, nickname, added_at, last_seen, is_blocked)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (user_id, public_key, nickname, time.time(), time.time()),
        )
        await db.commit()
        logger.info("Contact saved: %s (%s)", user_id[:8], nickname or "no nickname")

    async def get_contact(self, user_id: str) -> Optional[ContactRecord]:
        """Get a contact by user ID."""
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM contacts WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return ContactRecord(
            user_id=row["user_id"],
            public_key=row["public_key"],
            nickname=row["nickname"],
            added_at=row["added_at"],
            last_seen=row["last_seen"],
            is_blocked=bool(row["is_blocked"]),
        )

    async def get_all_contacts(self) -> list[ContactRecord]:
        """Get all contacts."""
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM contacts ORDER BY nickname ASC")
        rows = await cursor.fetchall()
        return [
            ContactRecord(
                user_id=row["user_id"],
                public_key=row["public_key"],
                nickname=row["nickname"],
                added_at=row["added_at"],
                last_seen=row["last_seen"],
                is_blocked=bool(row["is_blocked"]),
            )
            for row in rows
        ]

    async def remove_contact(self, user_id: str) -> None:
        """Remove a contact."""
        db = self._require_db()
        await db.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))
        await db.commit()

    async def update_contact_nickname(self, user_id: str, nickname: str) -> None:
        """Update a contact's nickname."""
        db = self._require_db()
        await db.execute(
            "UPDATE contacts SET nickname = ? WHERE user_id = ?",
            (nickname, user_id),
        )
        await db.commit()

    async def update_contact_last_seen(self, user_id: str, last_seen: float) -> None:
        """Update a contact's last seen timestamp."""
        db = self._require_db()
        await db.execute(
            "UPDATE contacts SET last_seen = ? WHERE user_id = ?",
            (last_seen, user_id),
        )
        await db.commit()

    # -------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------

    async def save_message(self, message: ChatMessage) -> None:
        """Save a message to the database."""
        db = self._require_db()
        peer_id = message.recipient_user_id if message.is_mine else message.sender_user_id
        await db.execute(
            """INSERT OR REPLACE INTO messages
               (message_id, sender_user_id, recipient_user_id, content,
                timestamp, status, reply_to, is_mine, session_peer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message.message_id_hex,
                message.sender_user_id,
                message.recipient_user_id,
                self._encrypt_content(message),
                message.timestamp,
                int(message.status),
                message.reply_to.hex() if message.reply_to else "",
                int(message.is_mine),
                peer_id,
            ),
        )
        await db.commit()

    async def get_messages(
        self,
        peer_user_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ChatMessage]:
        """Get messages for a conversation with a peer."""
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM messages
               WHERE session_peer_id = ?
               ORDER BY timestamp ASC
               LIMIT ? OFFSET ?""",
            (peer_user_id, limit, offset),
        )
        rows = await cursor.fetchall()

        messages: list[ChatMessage] = []
        for row in rows:
            reply_to = b""
            if row["reply_to"]:
                try:
                    reply_to = bytes.fromhex(row["reply_to"])
                except ValueError:
                    pass

            msg = ChatMessage(
                message_id=bytes.fromhex(row["message_id"]),
                sender_user_id=row["sender_user_id"],
                recipient_user_id=row["recipient_user_id"],
                content=self._decrypt_content(row),
                timestamp=row["timestamp"],
                status=MessageStatus(row["status"]),
                reply_to=reply_to,
                is_mine=bool(row["is_mine"]),
            )
            messages.append(msg)
        return messages

    async def update_message_status(self, message_id: str, status: MessageStatus) -> None:
        """Update the delivery status of a message."""
        db = self._require_db()
        await db.execute(
            "UPDATE messages SET status = ? WHERE message_id = ?",
            (int(status), message_id),
        )
        await db.commit()

    async def get_last_message(self, peer_user_id: str) -> Optional[ChatMessage]:
        """Get the most recent message in a conversation."""
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM messages
               WHERE session_peer_id = ?
               ORDER BY timestamp DESC
               LIMIT 1""",
            (peer_user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        reply_to = b""
        if row["reply_to"]:
            try:
                reply_to = bytes.fromhex(row["reply_to"])
            except ValueError:
                pass

        return ChatMessage(
            message_id=bytes.fromhex(row["message_id"]),
            sender_user_id=row["sender_user_id"],
            recipient_user_id=row["recipient_user_id"],
            content=self._decrypt_content(row),
            timestamp=row["timestamp"],
            status=MessageStatus(row["status"]),
            reply_to=reply_to,
            is_mine=bool(row["is_mine"]),
        )

    async def get_unread_count(self, peer_user_id: str) -> int:
        """Get the count of unread messages from a peer."""
        db = self._require_db()
        cursor = await db.execute(
            """SELECT COUNT(*) as cnt FROM messages
               WHERE session_peer_id = ? AND is_mine = 0 AND status < ?""",
            (peer_user_id, int(MessageStatus.READ)),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # File transfers
    # -------------------------------------------------------------------

    async def save_file_transfer(
        self,
        session_id: str,
        peer_user_id: str,
        file_name: str,
        file_size: int,
        file_sha256: bytes,
        direction: str,
        local_path: str = "",
    ) -> None:
        """Save a file transfer record."""
        import time

        db = self._require_db()
        await db.execute(
            """INSERT OR REPLACE INTO file_transfers
               (session_id, peer_user_id, file_name, file_size, file_sha256,
                direction, status, local_path, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0)""",
            (
                session_id,
                peer_user_id,
                file_name,
                file_size,
                file_sha256,
                direction,
                local_path,
                time.time(),
            ),
        )
        await db.commit()

    async def update_file_transfer_status(
        self, session_id: str, status: str, completed_at: float = 0.0
    ) -> None:
        """Update the status of a file transfer."""
        db = self._require_db()
        await db.execute(
            """UPDATE file_transfers
               SET status = ?, completed_at = ?
               WHERE session_id = ?""",
            (status, completed_at, session_id),
        )
        await db.commit()

    # -------------------------------------------------------------------
    # DHT routing
    # -------------------------------------------------------------------

    async def save_dht_node(
        self, user_id: str, addr: str, public_key: bytes, bucket_index: int
    ) -> None:
        """Save or update a DHT routing table entry."""
        import time

        db = self._require_db()
        await db.execute(
            """INSERT OR REPLACE INTO dht_routing
               (user_id, addr, public_key, last_seen, bucket_index)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, addr, public_key, time.time(), bucket_index),
        )
        await db.commit()

    async def get_dht_nodes(self) -> list[dict]:
        """Get all DHT routing entries."""
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM dht_routing")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------

    async def get_setting(self, key: str, default: str = "") -> str:
        """Get a setting value by key."""
        db = self._require_db()
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        """Set a setting value."""
        db = self._require_db()
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()
