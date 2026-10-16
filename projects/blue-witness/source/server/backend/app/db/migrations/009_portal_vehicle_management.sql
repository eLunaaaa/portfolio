-- 별명은 사용자별로 저장하여 공동 소유자의 화면에 영향을 주지 않음.
-- 기존 별명은 차량 목록 조회 시 vehicles.display_name으로 대체함.
CREATE TABLE IF NOT EXISTS vehicle_operation_grants (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('register', 'update', 'delete')),
    vehicle_id TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_vehicle_operation_grants_user
ON vehicle_operation_grants(user_id, purpose, vehicle_id);
