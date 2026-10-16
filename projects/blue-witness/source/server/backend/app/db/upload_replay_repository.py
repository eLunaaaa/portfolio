from dataclasses import dataclass

from app.db.database import (
    get_database_connection,
)


@dataclass(frozen=True)
class ExistingEvidenceUpload:
    """
    이미 DB에 저장된 차량 영상 정보를 표현함.

    Pi가 같은 영상을 재전송했을 때
    기존 저장 결과를 다시 응답하기 위해 사용함.
    """

    event_id: str
    vehicle_id: str

    accident_vehicle_id: str
    event_time_ns: int

    clip_start_time_ns: int
    clip_end_time_ns: int

    file_path: str

    event_status: str
    evidence_count: int

    collection_deadline_at: str | None
    merge_job_status: str | None

    content_sha256: str | None = None
    upload_status: str = "STORED"


def find_existing_evidence_upload(
    event_id: str,
    vehicle_id: str,
) -> ExistingEvidenceUpload | None:
    """
    event_id와 vehicle_id가 같은 기존 영상을 조회함.

    우리 시스템은 사고 하나에 대해 차량 한 대가
    영상 하나만 전송하도록 설계되어 있음.

    따라서 다음 두 값이 서버의 자연스러운
    중복 판별 키가 됨.

        event_id + vehicle_id

    저장된 영상이 없으면 None을 반환함.
    """

    connection = (
        get_database_connection()
    )

    try:
        row = connection.execute(
            """
            SELECT
                evidence.event_id,
                evidence.vehicle_id,
                evidence.clip_start_time_ns,
                evidence.clip_end_time_ns,
                evidence.file_path,
                evidence.content_sha256,
                evidence.upload_status,

                events.accident_vehicle_id,
                events.event_time_ns,
                events.status AS event_status,
                events.evidence_count,
                events.collection_deadline_at,

                merge_jobs.status
                    AS merge_job_status

            FROM evidence

            INNER JOIN events
                ON events.event_id
                   = evidence.event_id

            LEFT JOIN merge_jobs
                ON merge_jobs.event_id
                   = evidence.event_id

            WHERE events.event_id = ?
              AND evidence.vehicle_id = ?
              AND evidence.upload_status IN ('STORED', 'PENDING')

            LIMIT 1
            """,
            (
                event_id,
                vehicle_id,
            ),
        ).fetchone()

        if row is None:
            return None

        if row["file_path"] is None:
            return None

        return ExistingEvidenceUpload(
            event_id=str(
                row["event_id"]
            ),
            vehicle_id=str(
                row["vehicle_id"]
            ),
            accident_vehicle_id=str(
                row["accident_vehicle_id"]
            ),
            event_time_ns=int(
                row["event_time_ns"]
            ),
            clip_start_time_ns=int(
                row["clip_start_time_ns"]
            ),
            clip_end_time_ns=int(
                row["clip_end_time_ns"]
            ),
            file_path=str(
                row["file_path"]
            ),
            content_sha256=row["content_sha256"],
            upload_status=str(row["upload_status"]),
            event_status=str(
                row["event_status"]
            ),
            evidence_count=int(
                row["evidence_count"]
            ),
            collection_deadline_at=(
                None
                if row[
                    "collection_deadline_at"
                ] is None
                else str(
                    row[
                        "collection_deadline_at"
                    ]
                )
            ),
            merge_job_status=(
                None
                if row[
                    "merge_job_status"
                ] is None
                else str(
                    row[
                        "merge_job_status"
                    ]
                )
            ),
        )

    finally:
        connection.close()
