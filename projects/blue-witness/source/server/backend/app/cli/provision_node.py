import argparse

from app.db.database import (
    initialize_database,
)
from app.services.node_auth_service import (
    provision_node,
)


def build_parser() -> (
    argparse.ArgumentParser
):
    """
    Pi 등록 CLI가 받을 옵션을 정의함.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Raspberry Pi 노드를 "
            "서버 차량과 연결함."
        )
    )

    parser.add_argument(
        "--node-id",
        required=True,
        help=(
            "Pi 고유 ID. "
            "예: NODE_PI_01"
        ),
    )

    parser.add_argument(
        "--vehicle-id",
        required=True,
        help=(
            "서버 차량 ID. "
            "예: CAR_01"
        ),
    )

    parser.add_argument(
        "--rotate",
        action="store_true",
        help=(
            "기존 Pi의 API Key를 "
            "새 키로 교체함."
        ),
    )

    return parser


def main() -> None:
    """
    Pi와 차량을 연결하고
    API Key 원문을 한 번 출력함.
    """

    parser = build_parser()
    arguments = parser.parse_args()

    initialize_database()

    try:
        provisioned = provision_node(
            node_id=arguments.node_id,
            vehicle_id=(
                arguments.vehicle_id
            ),
            rotate=arguments.rotate,
        )

    except ValueError as error:
        parser.error(str(error))

    print()
    print("Pi 노드 등록 완료")

    print(
        f"node_id: {provisioned.node_id}"
    )

    print(
        "vehicle_id: "
        f"{provisioned.vehicle_id}"
    )

    print(
        "api_key: "
        f"{provisioned.api_key}"
    )

    print()

    print(
        "중요: API Key 원문은 DB에 "
        "저장되지 않음."
    )

    print(
        "지금 안전한 위치에 "
        "복사해야 함."
    )


if __name__ == "__main__":
    main()