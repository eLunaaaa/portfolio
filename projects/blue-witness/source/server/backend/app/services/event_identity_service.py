from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import sqlite3

from app.db.database import get_database_connection


SAME_EVENT_TIME_TOLERANCE_NS = 2_000_000_000
EVENT_ID_PATTERN = re.compile(
    r"^EVT_(\d{8})_(\d{9})_(CAR_?\d{2,4})$"
)


class EventIdentityConflictError(ValueError):
    """같은 event_id에 서로 다른 사고 정보가 들어온 경우임."""


@dataclass(frozen=True)
class ResolvedEventIdentity:
    event_id: str
    created: bool


def normalize_event_id(event_id: str) -> str:
    """Pi가 생성한 사람이 읽을 수 있는 event_id 형식을 검증함."""

    normalized = event_id.strip().upper()

    if not normalized:
        raise ValueError("event_id가 비어 있음.")

    if len(normalized) > 64:
        raise ValueError("event_id는 64자를 초과할 수 없음.")

    match = EVENT_ID_PATTERN.fullmatch(normalized)

    if match is None:
        raise ValueError(
            "event_id는 EVT_YYYYMMDD_HHMMSSmmm_CARXX 형식이어야 함."
        )

    date_text, time_text, _ = match.groups()

    try:
        datetime.strptime(
            date_text + time_text,
            "%Y%m%d%H%M%S%f",
        )
    except ValueError as error:
        raise ValueError(
            "event_id의 날짜 또는 시각이 올바르지 않음."
        ) from error

    return normalized


def normalize_accident_vehicle_id(
    accident_vehicle_id: str,
) -> str:
    """V2V 숫자 차량 번호를 서버 표준 CAR_XX 형식으로 변환함."""

    normalized = accident_vehicle_id.strip().upper()

    if normalized.isdecimal():
        vehicle_number = int(normalized)

        if vehicle_number < 1 or vehicle_number > 9999:
            raise ValueError(
                "사고 차량 번호는 1~9999 범위여야 함."
            )

        return f"CAR_{vehicle_number:02d}"

    if not re.fullmatch(r"CAR_[0-9]{2,4}", normalized):
        raise ValueError(
            "accident_vehicle_id는 숫자 또는 CAR_XX 형식이어야 함."
        )

    return normalized


def resolve_or_create_event(
    event_id: str,
    accident_vehicle_id: str,
    event_time_ns: int,
) -> ResolvedEventIdentity:
    """
    Pi가 보낸 event_id를 서버 사건으로 원자적으로 등록함.

    같은 event_id의 후속 차량은 기존 사건을 사용함.
    사고 시각이 2초보다 크게 다르면 잘못된 병합을 막기 위해 거부함.
    """

    normalized_event_id = normalize_event_id(event_id)
    normalized_accident_vehicle_id = normalize_accident_vehicle_id(
        accident_vehicle_id
    )
    match = EVENT_ID_PATTERN.fullmatch(normalized_event_id)
    assert match is not None
    identifier_time = datetime.strptime(
        match.group(1) + match.group(2),
        "%Y%m%d%H%M%S%f",
    ).replace(tzinfo=timezone(timedelta(hours=9)))
    identifier_time_ns = int(identifier_time.timestamp() * 1_000_000_000)

    if abs(identifier_time_ns - event_time_ns) > SAME_EVENT_TIME_TOLERANCE_NS:
        raise EventIdentityConflictError(
            "event_id의 시각과 event_time_ns가 2초 이상 차이남."
        )

    embedded_vehicle_id = normalize_accident_vehicle_id(
        match.group(3).removeprefix("CAR_").removeprefix("CAR")
    )

    if embedded_vehicle_id != normalized_accident_vehicle_id:
        raise EventIdentityConflictError(
            "event_id에 포함된 차량 번호와 accident_vehicle_id가 다름."
        )

    connection = get_database_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        row = connection.execute(
            """
            SELECT event_id, event_time_ns, accident_vehicle_id
            FROM events
            WHERE event_id = ?
            LIMIT 1
            """,
            (normalized_event_id,),
        ).fetchone()

        if row is not None:
            difference_ns = abs(
                int(row["event_time_ns"]) - event_time_ns
            )

            if difference_ns > SAME_EVENT_TIME_TOLERANCE_NS:
                raise EventIdentityConflictError(
                    "같은 event_id의 사고 시각이 기존 사건과 "
                    "2초 이상 차이남."
                )

            if (
                str(row["accident_vehicle_id"])
                != normalized_accident_vehicle_id
            ):
                raise EventIdentityConflictError(
                    "같은 event_id의 사고 차량 정보가 기존 사건과 다름."
                )

            connection.commit()
            return ResolvedEventIdentity(
                event_id=str(row["event_id"]),
                created=False,
            )

        connection.execute(
            """
            -- action_id는 기존 운영 DB 제약을 만족시키는
            -- 레거시 미러 컬럼이며 외부 API에는 노출하지 않음.
            INSERT INTO events (
                event_id,
                action_id,
                accident_vehicle_id,
                event_time_ns,
                status,
                evidence_count
            )
            VALUES (?, ?, ?, ?, 'COLLECTING', 0)
            """,
            (
                normalized_event_id,
                normalized_event_id,
                normalized_accident_vehicle_id,
                event_time_ns,
            ),
        )

        connection.commit()

        return ResolvedEventIdentity(
            event_id=normalized_event_id,
            created=True,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def remove_empty_created_event(event_id: str) -> None:
    """파일 저장이 실패한 신규 빈 사건만 안전하게 정리함."""

    connection = get_database_connection()

    try:
        connection.execute(
            """
            DELETE FROM events
            WHERE event_id = ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM evidence
                  WHERE evidence.event_id = events.event_id
              )
            """,
            (event_id,),
        )
        connection.commit()
    finally:
        connection.close()
