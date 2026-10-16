from datetime import (
    datetime,
    timedelta,
    timezone,
)
import os

from app.db.evidence_repository import (
    CollectionRefreshResult,
    EvidenceRegistrationResult,
    promote_due_collections,
    register_stored_evidence,
)
from app.models.evidence import (
    EvidenceMetadata,
)


def read_positive_integer(
    environment_name: str,
    default_value: int,
) -> int:
    """
    환경변수에서 양의 정수를 읽음.

    값이 없으면 기본값을 사용함.
    0 이하 또는 정수가 아니면 서버 시작을
    중단하여 잘못된 설정을 조기에 발견함.
    """

    legacy_name = environment_name.replace(
        "EVIDENCE_", "ACE_", 1
    )
    raw_value = os.getenv(
        environment_name,
        os.getenv(legacy_name),
    )

    if raw_value is None:
        return default_value

    try:
        parsed_value = int(
            raw_value
        )

    except ValueError as error:
        raise RuntimeError(
            f"{environment_name}은 "
            "정수여야 함."
        ) from error

    if parsed_value <= 0:
        raise RuntimeError(
            f"{environment_name}은 "
            "0보다 커야 함."
        )

    return parsed_value


COLLECTION_WINDOW_SECONDS = (
    read_positive_integer(
        "EVIDENCE_COLLECTION_WINDOW_SECONDS",
        12,
    )
)

MINIMUM_EVIDENCE_COUNT = (
    read_positive_integer(
        "EVIDENCE_MINIMUM_EVIDENCE_COUNT",
        1,
    )
)

COLLECTION_RECHECK_SECONDS = (
    read_positive_integer(
        "EVIDENCE_COLLECTION_RECHECK_SECONDS",
        10,
    )
)


def datetime_to_database_text(
    value: datetime,
) -> str:
    """
    UTC datetime을 DB 저장용 ISO 문자열로 변환함.

    모든 시각을 UTC로 통일하여 서버의 지역 설정이
    달라져도 비교 결과가 달라지지 않게 함.
    """

    return (
        value
        .astimezone(timezone.utc)
        .isoformat(
            timespec="milliseconds"
        )
    )


def register_evidence_for_collection(
    metadata: EvidenceMetadata,
    event_id: str,
    file_path: str,
    content_sha256: str | None = None,
    repair: bool = False,
) -> EvidenceRegistrationResult:
    """
    새 영상을 사고 수집 과정에 등록함.

    첫 영상이라면 현재 시각을 기준으로
    수집 마감 시각도 함께 계산함.
    """

    current_time = datetime.now(
        timezone.utc
    )

    collection_deadline = (
        current_time
        + timedelta(
            seconds=(
                COLLECTION_WINDOW_SECONDS
            )
        )
    )

    return register_stored_evidence(
        metadata=metadata,
        event_id=event_id,
        file_path=file_path,
        content_sha256=content_sha256,
        repair=repair,
        received_at=(
            datetime_to_database_text(
                current_time
            )
        ),
        collection_deadline_at=(
            datetime_to_database_text(
                collection_deadline
            )
        ),
        minimum_evidence_count=(
            MINIMUM_EVIDENCE_COUNT
        ),
    )


def refresh_collection_states() -> (
    CollectionRefreshResult
):
    """
    수집 마감 시각이 지난 사고를 확인함.

    현재 2단계에서는 CLI가 이 함수를 호출함.
    3단계에서는 자동 Worker가 일정 주기로 호출함.
    """

    current_time = datetime.now(
        timezone.utc
    )

    retry_time = (
        current_time
        + timedelta(
            seconds=(
                COLLECTION_RECHECK_SECONDS
            )
        )
    )

    return promote_due_collections(
        current_time=(
            datetime_to_database_text(
                current_time
            )
        ),
        retry_at=(
            datetime_to_database_text(
                retry_time
            )
        ),
        minimum_evidence_count=(
            MINIMUM_EVIDENCE_COUNT
        ),
    )
