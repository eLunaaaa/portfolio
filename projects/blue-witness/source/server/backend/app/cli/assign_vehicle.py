import argparse
import sys

from app.services.account_service import (
    assign_vehicle,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "차량을 생성하고 사용자에게 할당함."
        )
    )

    parser.add_argument(
        "username",
        help="차량을 할당받을 사용자 아이디",
    )

    parser.add_argument(
        "vehicle_id",
        help="내부 차량 ID. 예: CAR_01",
    )

    parser.add_argument(
        "license_plate",
        help="사용자에게 표시할 차량번호",
    )

    parser.add_argument(
        "--name",
        default=None,
        help="차량 표시 이름",
    )

    arguments = parser.parse_args()

    try:
        assign_vehicle(
            username=arguments.username,
            vehicle_id=arguments.vehicle_id,
            license_plate=(
                arguments.license_plate
            ),
            display_name=arguments.name,
        )

    except Exception as error:
        print(f"차량 할당 실패: {error}")
        sys.exit(1)

    print()
    print("차량 할당 완료")
    print(
        f"- 사용자: {arguments.username}"
    )
    print(
        f"- 차량 ID: {arguments.vehicle_id}"
    )
    print(
        f"- 차량번호: {arguments.license_plate}"
    )


if __name__ == "__main__":
    main()
