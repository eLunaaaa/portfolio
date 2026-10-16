-- 이전 데이터베이스의 사건 식별자 컬럼을 호환함.
-- 실제 컬럼 보정과 기존 데이터 채우기는 database.py에서 수행함.

CREATE INDEX IF NOT EXISTS idx_events_event_time_ns
ON events(event_time_ns);
