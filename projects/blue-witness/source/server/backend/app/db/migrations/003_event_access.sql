PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS event_access (
    event_id TEXT PRIMARY KEY,

    code_hash TEXT NOT NULL,

    expires_at TEXT NOT NULL,

    failed_attempts INTEGER NOT NULL DEFAULT 0,

    locked_until TEXT,

    revoked_at TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (event_id)
        REFERENCES events(event_id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS access_sessions (
    token_hash TEXT PRIMARY KEY,

    event_id TEXT NOT NULL,

    expires_at TEXT NOT NULL,

    revoked_at TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (event_id)
        REFERENCES events(event_id)
        ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS
idx_access_sessions_event_id
ON access_sessions(event_id);


CREATE INDEX IF NOT EXISTS
idx_access_sessions_expires_at
ON access_sessions(expires_at);