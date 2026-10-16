import time

from app.db.database import (
    get_database_connection,
    initialize_database,
)


def main() -> None:
    """
    테스트 사고와 증거 데이터를 저장하고
    다시 조회하여 DB 동작을 확인함.
    """

    initialize_database()

    now_ns = time.time_ns()

    event_id = f"EVT_TEST_{now_ns}"
    vehicle_id = "CAR_TEST"

    clip_start_time_ns = (
        now_ns - 5_000_000_000
    )

    clip_end_time_ns = (
        now_ns + 5_000_000_000
    )

    connection = get_database_connection()

    try:
        with connection:
            connection.execute(
                """
                INSERT INTO events (
                    event_id,
                    accident_vehicle_id,
                    event_time_ns,
                    status
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    event_id,
                    "CAR_ACCIDENT",
                    now_ns,
                    "COLLECTING",
                ),
            )

            connection.execute(
                """
                INSERT INTO evidence (
                    event_id,
                    vehicle_id,
                    clip_start_time_ns,
                    clip_end_time_ns,
                    upload_status,
                    time_sync_status,
                    chrony_offset_ms,
                    ai_confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    vehicle_id,
                    clip_start_time_ns,
                    clip_end_time_ns,
                    "PENDING",
                    "SYNCED",
                    1.25,
                    0.91,
                ),
            )

        row = connection.execute(
            """
            SELECT
                events.event_id,
                events.accident_vehicle_id,
                events.event_time_ns,
                events.status AS event_status,
                evidence.vehicle_id,
                evidence.upload_status,
                evidence.time_sync_status,
                evidence.chrony_offset_ms,
                evidence.ai_confidence
            FROM events
            JOIN evidence
              ON events.event_id = evidence.event_id
            WHERE events.event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "저장한 테스트 데이터를 조회할 수 없음."
            )

        print("SQLite 테스트 성공")
        print(f"event_id: {row['event_id']}")
        print(
            "accident_vehicle_id: "
            f"{row['accident_vehicle_id']}"
        )
        print(
            f"event_status: {row['event_status']}"
        )
        print(f"vehicle_id: {row['vehicle_id']}")
        print(
            f"upload_status: {row['upload_status']}"
        )
        print(
            "time_sync_status: "
            f"{row['time_sync_status']}"
        )
        print(
            "chrony_offset_ms: "
            f"{row['chrony_offset_ms']}"
        )
        print(
            "ai_confidence: "
            f"{row['ai_confidence']}"
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()