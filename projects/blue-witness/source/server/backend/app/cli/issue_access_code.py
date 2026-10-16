import argparse
import sys

from app.services.access_service import (
    issue_access_code,
)


def create_argument_parser() -> argparse.ArgumentParser:
    """
    조회 코드 발급 명령의 입력값을 정의함.
    """

    parser = argparse.ArgumentParser(
        description=(
            "기존 사고 이벤트에 "
            "사고 조회 코드를 발급함."
        )
    )

    parser.add_argument(
        "event_id",
        help="조회 코드를 발급할 사고 ID",
    )

    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="조회 코드 유효시간. 기본값은 24시간임.",
    )

    return parser


def main() -> None:
    """
    조회 코드 발급 CLI의 시작점임.

    원본 조회 코드는 이 화면에서만 확인할 수 있으므로
    사고 당사자에게 전달해야 함.
    """

    parser = create_argument_parser()
    arguments = parser.parse_args()

    try:
        issued = issue_access_code(
            event_id=arguments.event_id,
            valid_hours=arguments.hours,
        )

    except Exception as error:
        print()
        print("조회 코드 발급 실패")
        print(str(error))
        sys.exit(1)

    print()
    print("사고 조회 코드 발급 완료")
    print(f"- EVENT ID: {issued.event_id}")
    print(
        f"- 조회 코드: {issued.access_code}"
    )
    print(
        f"- 만료시각(UTC): "
        f"{issued.expires_at}"
    )
    print()
    print(
        "주의: 원본 조회 코드는 DB에 저장되지 않으므로 "
        "지금 사고 당사자에게 전달해야 함."
    )


if __name__ == "__main__":
    main()
