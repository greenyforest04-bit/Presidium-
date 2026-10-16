"""Database schema definitions for Presidium.

Uses raw aiosqlite with explicit SQL schema.
All tables are defined here with their creation SQL.
Schema version is tracked in a metadata table for future migrations.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

# ---------------------------------------------------------------------------
# SQL: Schema creation
# ---------------------------------------------------------------------------

CREATE_METADATA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_CONTACTS = """
CREATE TABLE IF NOT EXISTS contacts (
    user_id TEXT PRIMARY KEY,
    public_key BLOB NOT NULL,
    nickname TEXT DEFAULT '',
    added_at REAL NOT NULL,
    last_seen REAL DEFAULT 0.0,
    is_blocked INTEGER DEFAULT 0
);
"""

CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    sender_user_id TEXT NOT NULL,
    recipient_user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp REAL NOT NULL,
    status INTEGER DEFAULT 0,
    reply_to TEXT DEFAULT '',
    is_mine INTEGER DEFAULT 0,
    session_peer_id TEXT NOT NULL,
    FOREIGN KEY (session_peer_id) REFERENCES contacts(user_id)
);
"""

CREATE_INDEX_MESSAGES_SESSION = """
CREATE INDEX IF NOT EXISTS idx_messages_session_peer
ON messages(session_peer_id, timestamp);
"""

CREATE_INDEX_MESSAGES_TIMESTAMP = """
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
ON messages(timestamp);
"""

# FTS5 full-text search index
CREATE_MESSAGES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id,
    content,
    content='messages',
    content_rowid='rowid'
);
"""

# Triggers to keep FTS in sync
CREATE_MESSAGES_FTS_INSERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages
BEGIN
    INSERT INTO messages_fts(rowid, message_id, content)
    VALUES (new.rowid, new.message_id, new.content);
END;
"""

CREATE_MESSAGES_FTS_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages
BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message_id, content)
    VALUES ('delete', old.rowid, old.message_id, old.content);
END;
"""

CREATE_MESSAGES_FTS_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages
BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message_id, content)
    VALUES ('delete', old.rowid, old.message_id, old.content);
    INSERT INTO messages_fts(rowid, message_id, content)
    VALUES (new.rowid, new.message_id, new.content);
END;
"""

CREATE_FILE_TRANSFERS = """
CREATE TABLE IF NOT EXISTS file_transfers (
    session_id TEXT PRIMARY KEY,
    peer_user_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_sha256 BLOB NOT NULL,
    direction TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    local_path TEXT DEFAULT '',
    created_at REAL NOT NULL,
    completed_at REAL DEFAULT 0.0,
    FOREIGN KEY (peer_user_id) REFERENCES contacts(user_id)
);
"""

CREATE_DHT_ROUTING = """
CREATE TABLE IF NOT EXISTS dht_routing (
    user_id TEXT PRIMARY KEY,
    addr TEXT NOT NULL,
    public_key BLOB DEFAULT '',
    last_seen REAL NOT NULL,
    bucket_index INTEGER DEFAULT 0
);
"""

CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Reactions table
CREATE_REACTIONS = """
CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(message_id, user_id, emoji),
    FOREIGN KEY (message_id) REFERENCES messages(message_id)
);
"""

CREATE_INDEX_REACTIONS_MESSAGE = """
CREATE INDEX IF NOT EXISTS idx_reactions_message
ON reactions(message_id);
"""

# Outbox table (messages pending delivery)
CREATE_OUTBOX = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_user_id TEXT NOT NULL,
    message_id BLOB NOT NULL,
    encrypted_payload BLOB NOT NULL,
    created_at REAL NOT NULL,
    last_retry_at REAL DEFAULT 0.0,
    retry_count INTEGER DEFAULT 0,
    delivered INTEGER DEFAULT 0
);
"""

CREATE_INDEX_OUTBOX_PEER = """
CREATE INDEX IF NOT EXISTS idx_outbox_peer
ON outbox(peer_user_id, delivered);
"""

# Ratchet state persistence
CREATE_RATCHET_STATES = """
CREATE TABLE IF NOT EXISTS ratchet_states (
    peer_user_id TEXT PRIMARY KEY,
    state_data BLOB NOT NULL,
    updated_at REAL NOT NULL
);
"""

# All creation statements in order
ALL_CREATE_STATEMENTS = [
    CREATE_METADATA,
    CREATE_CONTACTS,
    CREATE_MESSAGES,
    CREATE_INDEX_MESSAGES_SESSION,
    CREATE_INDEX_MESSAGES_TIMESTAMP,
    CREATE_FILE_TRANSFERS,
    CREATE_DHT_ROUTING,
    CREATE_SETTINGS,
    CREATE_REACTIONS,
    CREATE_INDEX_REACTIONS_MESSAGE,
    CREATE_OUTBOX,
    CREATE_INDEX_OUTBOX_PEER,
    CREATE_RATCHET_STATES,
]
