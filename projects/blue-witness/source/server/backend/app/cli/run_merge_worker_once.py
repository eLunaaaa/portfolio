import argparse
from dataclasses import asdict
import json

from app.db.database import (
    initialize_database,
)
from app.db.merge_job_repository import (
    recover_interrupted_merge_jobs,
)
from app.services.merge_worker import (
    current_utc_text,
    run_merge_worker_cycle,
)


def build_parser() -> (
    argparse.ArgumentParser
):
    """
    단일 Worker 테스트 명령의 옵션을 정의함.
    """

    parser = argparse.ArgumentParser(
        description=(
            "자동 병합 Worker를 "
            "한 주기만 실행함."
        )
    )

    parser.add_argument(
        "--recover-interrupted",
        action="store_true",
        help=(
            "RUNNING에 남은 작업을 "
            "PENDING으로 복구한 후 실행함."
        ),
    )

    return parser


def main() -> int:
    """
    병합 Worker를 한 번 실행하고
    결과를 JSON으로 출력함.

    서버 Worker가 실행 중일 때는 이 CLI를
    동시에 실행하지 않는 것이 안전함.
    """

    parser = build_parser()
    arguments = parser.parse_args()

    initialize_database()

    if arguments.recover_interrupted:
        recovered = (
            recover_interrupted_merge_jobs(
                current_utc_text()
            )
        )

        print(
            "복구한 작업:",
            len(recovered),
        )

        for event_id in recovered:
            print(
                f"- {event_id}"
            )

    result = run_merge_worker_cycle()

    print(
        json.dumps(
            asdict(result),
            ensure_ascii=False,
            indent=2,
        )
    )

    if result.state == "FAILED":
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )