from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
import secrets

from app.db.database import (
    get_database_connection,
)
from app.services.node_auth_service import (
    normalize_node_id,
)
from app.services.portal_service import (
    find_merged_video,
)
from app.services.storage import (
    PROJECT_ROOT,
    calculate_file_sha256,
    normalize_sha256,
)


@dataclass(frozen=True)
class FailSafeVerificationReport:
    """
    공유기 단절·복구 시험 이후 서버에 남은
    사고 파이프라인 상태를 한 번에 표현함.

    verified=True는 다음 핵심 조건을 뜻함.

    - Pi와 차량 매칭이 활성 상태임.
    - 해당 차량의 evidence가 STORED 상태임.
    - DB 경로의 실제 MP4가 존재하고 비어 있지 않음.
    - 선택적으로 전달한 Pi SHA-256과 서버 파일이 같음.
    - events.evidence_count가 실제 STORED 차량 수와 같음.
    - 사고와 자동 병합 작업 상태 조합이 모순되지 않음.
    """

    verified: bool

    node_id: str
    vehicle_id: str | None
    event_id: str

    upload_status: str | None
    file_path: str | None
    file_size_bytes: int | None
    sha256: str | None

    event_status: str | None
    recorded_evidence_count: int | None
    actual_evidence_count: int | None
    merge_job_status: str | None

    merged_video_path: str | None
    merged_mode: str | None

    problems: tuple[str, ...]

    def to_dict(self) -> dict:
        """
        CLI가 JSON으로 출력할 수 있도록
        dataclass를 일반 dict로 변환함.
        """

        return asdict(self)


def pipeline_state_problem(
    event_status: str,
    merge_job_status: str | None,
) -> str | None:
    """
    사고 상태와 병합 작업 상태의 조합이
    서버 자동화 규칙에 맞는지 확인함.

    수집 중에는 WAITING 작업을 허용하고,
    마감 후에는 READY/PENDING,
    병합 중에는 MERGING/RUNNING,
    완료·실패 상태도 서로 짝이 맞아야 함.
    """

    allowed_job_statuses = {
        "COLLECTING": {
            None,
            "WAITING",
        },
        "READY": {
            "PENDING",
        },
        "MERGING": {
            "RUNNING",
        },
        "COMPLETED": {
            "COMPLETED",
        },
        "FAILED": {
            "FAILED",
        },
    }

    expected_statuses = (
        allowed_job_statuses.get(
            event_status
        )
    )

    if expected_statuses is None:
        return (
            "알 수 없는 사고 상태임: "
            f"{event_status}"
        )

    if merge_job_status not in expected_statuses:
        return (
            "사고 상태와 병합 작업 상태가 "
            "일치하지 않음: "
            f"event={event_status}, "
            f"job={merge_job_status}"
        )

    return None


def verify_fail_safe_event(
    node_id: str,
    event_id: str,
    expected_sha256: str | None = None,
) -> FailSafeVerificationReport:
    """
    물리적인 네트워크 단절·복구 시험 후
    서버 저장 결과와 파이프라인 상태를 검증함.

    이 함수는 데이터를 수정하지 않고 조회만 함.
    API Key 원문이나 해시값도 출력하지 않음.

    expected_sha256를 전달하면 Pi 로컬 원본과
    서버 MP4가 같은 파일인지까지 확인함.
    """

    normalized_node_id = (
        normalize_node_id(node_id)
    )

    normalized_event_id = (
        event_id.strip()
    )

    if not normalized_event_id:
        raise ValueError(
            "event_id가 필요함."
        )

    normalized_expected_sha256 = None

    if expected_sha256 is not None:
        normalized_expected_sha256 = (
            normalize_sha256(
                expected_sha256
            )
        )

    problems: list[str] = []

    vehicle_id: str | None = None
    upload_status: str | None = None
    file_path: str | None = None
    file_size_bytes: int | None = None
    actual_sha256: str | None = None
    event_status: str | None = None
    recorded_evidence_count: int | None = None
    actual_evidence_count: int | None = None
    merge_job_status: str | None = None
    merged_video_path: str | None = None
    merged_mode: str | None = None

    connection = get_database_connection()

    try:
        # API Key 해시를 조회하거나 출력하지 않고
        # 활성 노드와 차량 매칭만 확인함.
        node_row = connection.execute(
            """
            SELECT vehicle_id, is_active
            FROM vehicle_node_credentials
            WHERE node_id = ?
            """,
            (normalized_node_id,),
        ).fetchone()

        if node_row is None:
            problems.append(
                "등록된 Pi 노드를 찾을 수 없음."
            )

        else:
            vehicle_id = str(
                node_row["vehicle_id"]
            )

            if not bool(
                node_row["is_active"]
            ):
                problems.append(
                    "Pi 노드가 비활성 상태임."
                )

        event_row = connection.execute(
            """
            SELECT status, evidence_count
            FROM events
            WHERE event_id = ?
            """,
            (normalized_event_id,),
        ).fetchone()

        if event_row is None:
            problems.append(
                "사고를 찾을 수 없음."
            )

        else:
            event_status = str(
                event_row["status"]
            )
            recorded_evidence_count = int(
                event_row["evidence_count"]
            )

        evidence_row = None

        if vehicle_id is not None:
            evidence_row = connection.execute(
                """
                SELECT upload_status, file_path
                FROM evidence
                WHERE event_id = ?
                  AND vehicle_id = ?
                """,
                (
                    normalized_event_id,
                    vehicle_id,
                ),
            ).fetchone()

        if evidence_row is None:
            problems.append(
                "해당 Pi 차량의 evidence가 없음."
            )

        else:
            upload_status = str(
                evidence_row[
                    "upload_status"
                ]
            )

            if upload_status != "STORED":
                problems.append(
                    "evidence가 STORED 상태가 아님: "
                    f"{upload_status}"
                )

            raw_file_path = (
                evidence_row["file_path"]
            )

            if raw_file_path is None:
                problems.append(
                    "evidence 파일 경로가 없음."
                )
            else:
                file_path = str(
                    raw_file_path
                )

        count_row = connection.execute(
            """
            SELECT COUNT(
                DISTINCT vehicle_id
            ) AS stored_count
            FROM evidence
            WHERE event_id = ?
              AND upload_status = 'STORED'
            """,
            (normalized_event_id,),
        ).fetchone()

        actual_evidence_count = int(
            count_row["stored_count"]
        )

        if (
            recorded_evidence_count
            is not None
            and recorded_evidence_count
            != actual_evidence_count
        ):
            problems.append(
                "events.evidence_count와 실제 "
                "STORED 차량 수가 다름."
            )

        merge_job_row = connection.execute(
            """
            SELECT status
            FROM merge_jobs
            WHERE event_id = ?
            """,
            (normalized_event_id,),
        ).fetchone()

        if merge_job_row is not None:
            merge_job_status = str(
                merge_job_row["status"]
            )

    finally:
        connection.close()

    if file_path is not None:
        # DB에 절대경로나 ../가 들어 있어도 프로젝트
        # 외부 파일을 검사하지 않도록 경계를 확인함.
        resolved_project_root = (
            PROJECT_ROOT.resolve()
        )
        resolved_file_path = (
            resolved_project_root
            / file_path
        ).resolve()

        try:
            resolved_file_path.relative_to(
                resolved_project_root
            )
        except ValueError:
            problems.append(
                "영상 경로가 서버 저장소 밖을 가리킴."
            )
        else:
            if not resolved_file_path.is_file():
                problems.append(
                    "서버 MP4 파일을 찾을 수 없음."
                )
            else:
                file_size_bytes = (
                    resolved_file_path
                    .stat()
                    .st_size
                )

                if file_size_bytes <= 0:
                    problems.append(
                        "서버 MP4 파일이 비어 있음."
                    )
                else:
                    actual_sha256 = (
                        calculate_file_sha256(
                            resolved_file_path
                        )
                    )

                    if (
                        normalized_expected_sha256
                        is not None
                        and not secrets.compare_digest(
                            actual_sha256,
                            normalized_expected_sha256,
                        )
                    ):
                        problems.append(
                            "Pi 원본과 서버 MP4의 "
                            "SHA-256이 다름."
                        )

    if event_status is not None:
        state_problem = (
            pipeline_state_problem(
                event_status,
                merge_job_status,
            )
        )

        if state_problem is not None:
            problems.append(
                state_problem
            )

    merged_result = find_merged_video(
        normalized_event_id
    )

    if merged_result is not None:
        merged_path, merged_mode = (
            merged_result
        )

        if merged_path is not None:
            merged_video_path = (
                merged_path
                .relative_to(PROJECT_ROOT)
                .as_posix()
            )

    if (
        event_status == "COMPLETED"
        and merged_video_path is None
    ):
        problems.append(
            "COMPLETED 사고의 병합 영상이 없음."
        )

    return FailSafeVerificationReport(
        verified=not problems,
        node_id=normalized_node_id,
        vehicle_id=vehicle_id,
        event_id=normalized_event_id,
        upload_status=upload_status,
        file_path=file_path,
        file_size_bytes=file_size_bytes,
        sha256=actual_sha256,
        event_status=event_status,
        recorded_evidence_count=(
            recorded_evidence_count
        ),
        actual_evidence_count=(
            actual_evidence_count
        ),
        merge_job_status=merge_job_status,
        merged_video_path=(
            merged_video_path
        ),
        merged_mode=merged_mode,
        problems=tuple(problems),
    )
