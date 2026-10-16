PRAGMA foreign_keys = ON;


-- 차량번호 변경 전후를 보존하는 감사 이력임.
CREATE TABLE IF NOT EXISTS vehicle_plate_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    old_license_plate TEXT NOT NULL,
    new_license_plate TEXT NOT NULL,
    changed_by_user_id INTEGER NOT NULL,
    change_reason TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(vehicle_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (changed_by_user_id)
        REFERENCES users(id)
        ON DELETE RESTRICT
);


CREATE INDEX IF NOT EXISTS idx_plate_history_vehicle
ON vehicle_plate_history(vehicle_id, changed_at);


-- Pi 교체 시 기존 노드 정보를 보존하는 감사 이력임.
-- API Key 원문과 해시는 기록하지 않고 끝 4자리만 남김.
CREATE TABLE IF NOT EXISTS vehicle_node_replacement_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    old_node_id TEXT,
    old_api_key_last4 TEXT,
    new_node_id TEXT NOT NULL,
    new_api_key_last4 TEXT NOT NULL,
    changed_by_user_id INTEGER NOT NULL,
    change_reason TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(vehicle_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (changed_by_user_id)
        REFERENCES users(id)
        ON DELETE RESTRICT
);


CREATE INDEX IF NOT EXISTS idx_node_replacement_vehicle
ON vehicle_node_replacement_history(vehicle_id, changed_at);
