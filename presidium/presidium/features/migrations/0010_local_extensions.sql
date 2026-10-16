CREATE TABLE social_comments (
 id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES social_posts(id),
 user_id TEXT NOT NULL REFERENCES users(id), text TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE social_follows (
 user_id TEXT NOT NULL REFERENCES users(id), target_id TEXT NOT NULL REFERENCES users(id),
 PRIMARY KEY(user_id,target_id)
);
CREATE TABLE capsule_entries (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 text TEXT NOT NULL, kind TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, deleted_at INTEGER
);
CREATE INDEX capsule_owner ON capsule_entries(user_id,deleted_at,updated_at);
