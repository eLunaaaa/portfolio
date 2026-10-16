PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    username TEXT NOT NULL
        COLLATE NOCASE
        UNIQUE,

    password_hash TEXT NOT NULL,

    full_name TEXT NOT NULL,

    role TEXT NOT NULL DEFAULT 'USER'
        CHECK (role IN ('USER', 'ADMIN')),

    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,

    user_id INTEGER NOT NULL,

    expires_at TEXT NOT NULL,

    revoked_at TEXT,

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id TEXT PRIMARY KEY,

    license_plate TEXT NOT NULL
        COLLATE NOCASE
        UNIQUE,

    display_name TEXT,

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS user_vehicles (
    user_id INTEGER NOT NULL,

    vehicle_id TEXT NOT NULL,

    assigned_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (
        user_id,
        vehicle_id
    ),

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(vehicle_id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS vehicle_nodes (
    vehicle_id TEXT PRIMARY KEY,

    ip_address TEXT,

    software_version TEXT,

    last_seen_at TEXT NOT NULL,

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP
);


CREATE INDEX IF NOT EXISTS
idx_user_sessions_user_id
ON user_sessions(user_id);


CREATE INDEX IF NOT EXISTS
idx_user_sessions_expires_at
ON user_sessions(expires_at);


CREATE INDEX IF NOT EXISTS
idx_user_vehicles_vehicle_id
ON user_vehicles(vehicle_id);


CREATE INDEX IF NOT EXISTS
idx_vehicle_nodes_last_seen_at
ON vehicle_nodes(last_seen_at);