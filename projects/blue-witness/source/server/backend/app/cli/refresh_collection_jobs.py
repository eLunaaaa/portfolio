from app.db.database import (
    initialize_database,
)
from app.services.collection_service import (
    MINIMUM_EVIDENCE_COUNT,
    refresh_collection_states,
)


def main() -> None:
    """
    마감 시각이 지난 사고를 확인하고
    병합 대기 상태로 전환함.

    현재 2단계에서는 수동으로 실행함.

    3단계에서는 서버의 자동 Worker가
    이 함수를 대신 주기적으로 실행함.
    """

    initialize_database()

    result = refresh_collection_states()

    print()
    print("사고 수집 상태 갱신 완료")

    print(
        "확인한 사고 수:",
        result.checked_count,
    )

    print(
        "최소 영상 수:",
        MINIMUM_EVIDENCE_COUNT,
    )

    print(
        "병합 대기로 전환:",
        len(
            result.promoted_event_ids
        ),
    )

    for event_id in (
        result.promoted_event_ids
    ):
        print(
            f"  READY: {event_id}"
        )

    print(
        "영상 부족으로 대기:",
        len(
            result.waiting_event_ids
        ),
    )

    for event_id in (
        result.waiting_event_ids
    ):
        print(
            f"  WAITING: {event_id}"
        )


if __name__ == "__main__":
    main()