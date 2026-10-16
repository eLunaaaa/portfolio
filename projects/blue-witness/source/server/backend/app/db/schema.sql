CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,

    -- 이전 DB와의 무중단 호환을 위한 레거시 미러 컬럼임.
    -- 신규 API는 event_id만 받고 응답하며 이 값을 노출하지 않음.
    action_id TEXT NOT NULL UNIQUE,

    accident_vehicle_id TEXT NOT NULL,

    event_time_ns INTEGER NOT NULL
        CHECK (event_time_ns > 0),

    status TEXT NOT NULL DEFAULT 'COLLECTING'
        CHECK (
            status IN (
                'COLLECTING',
                'READY',
                'MERGING',
                'COMPLETED',
                'FAILED'
            )
        ),

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    event_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,

    clip_start_time_ns INTEGER NOT NULL
        CHECK (clip_start_time_ns > 0),

    clip_end_time_ns INTEGER NOT NULL
        CHECK (clip_end_time_ns >= clip_start_time_ns),

    file_path TEXT,

    -- 최초 정상 업로드에서 검증한 원본 해시. 기존 자료는 확인 없이 채우지 않음.
    content_sha256 TEXT,

    upload_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (
            upload_status IN (
                'PENDING',
                'STORED',
                'FAILED'
            )
        ),

    time_sync_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (
            time_sync_status IN (
                'SYNCED',
                'HOLDOVER',
                'UNRELIABLE',
                'UNKNOWN'
            )
        ),

    chrony_offset_ms REAL,

    ai_confidence REAL
        CHECK (
            ai_confidence IS NULL
            OR (
                ai_confidence >= 0.0
                AND ai_confidence <= 1.0
            )
        ),

    received_at TEXT,

    FOREIGN KEY (event_id)
        REFERENCES events(event_id)
        ON DELETE CASCADE,

    UNIQUE (event_id, vehicle_id)
);


CREATE INDEX IF NOT EXISTS idx_events_event_time_ns
ON events(event_time_ns);


CREATE INDEX IF NOT EXISTS idx_evidence_upload_status
ON evidence(upload_status);
