PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS
vehicle_node_credentials (
    node_id TEXT PRIMARY KEY
        COLLATE NOCASE,

    vehicle_id TEXT NOT NULL
        UNIQUE,

    api_key_hash TEXT NOT NULL
        UNIQUE
        CHECK (
            LENGTH(api_key_hash) = 64
        ),

    api_key_last4 TEXT NOT NULL
        CHECK (
            LENGTH(api_key_last4) = 4
        ),

    is_active INTEGER NOT NULL
        DEFAULT 1
        CHECK (
            is_active IN (0, 1)
        ),

    last_authenticated_at TEXT,

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(vehicle_id)
        ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS
idx_node_credentials_vehicle_id
ON vehicle_node_credentials(
    vehicle_id
);


CREATE INDEX IF NOT EXISTS
idx_node_credentials_last_authenticated
ON vehicle_node_credentials(
    last_authenticated_at
);