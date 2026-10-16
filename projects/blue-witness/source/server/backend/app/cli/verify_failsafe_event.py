import argparse
import json

from app.db.database import (
    initialize_database,
)
from app.services.failsafe_verification import (
    verify_fail_safe_event,
)


def build_parser() -> argparse.ArgumentParser:
    """
    Fail-Safe 검증 명령의 입력값을 정의함.
    """

    parser = argparse.ArgumentParser(
        description=(
            "공유기 단절·복구 후 서버의 영상 저장과 "
            "자동 병합 파이프라인 상태를 검증함."
        )
    )

    parser.add_argument(
        "event_id",
        help="검증할 사고 ID",
    )

    parser.add_argument(
        "--node-id",
        required=True,
        help="영상을 보낸 Pi 노드 ID",
    )

    parser.add_argument(
        "--sha256",
        default=None,
        help=(
            "선택 사항. Pi 로컬 원본 MP4의 "
            "SHA-256을 전달하면 파일 동일성도 확인함."
        ),
    )

    return parser


def main() -> int:
    """
    검증 결과를 JSON으로 출력함.

    종료 코드 0:
        모든 조건을 통과함.

    종료 코드 1:
        누락·불일치·상태 모순이 있음.
    """

    arguments = build_parser().parse_args()

    initialize_database()

    report = verify_fail_safe_event(
        node_id=arguments.node_id,
        event_id=arguments.event_id,
        expected_sha256=arguments.sha256,
    )

    print(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0 if report.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
