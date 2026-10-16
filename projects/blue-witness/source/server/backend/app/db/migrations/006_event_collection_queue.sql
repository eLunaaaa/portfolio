PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS merge_jobs (
    event_id TEXT PRIMARY KEY,

    status TEXT NOT NULL
        DEFAULT 'WAITING'
        CHECK (
            status IN (
                'WAITING',
                'PENDING',
                'RUNNING',
                'COMPLETED',
                'FAILED'
            )
        ),

    available_at TEXT NOT NULL,

    attempt_count INTEGER NOT NULL
        DEFAULT 0
        CHECK (
            attempt_count >= 0
        ),

    last_error TEXT,

    started_at TEXT,

    completed_at TEXT,

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (event_id)
        REFERENCES events(event_id)
        ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS
idx_merge_jobs_status_available_at
ON merge_jobs(
    status,
    available_at
);
