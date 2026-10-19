# 파일 역할: 차량을 등록하고 사용자에게 할당하는 관리자 명령행 진입점.
import argparse
import sys

from app.services.account_service import (
    assign_vehicle,
)


# 기능: 차량을 등록하고 지정 사용자에게 할당한다.
# 입력: 명령행 사용자명·차량 ID·차량번호와 선택 표시 이름.
# 출력: 등록·할당 결과 출력; 반환값 없음.
# 구조·이유: CLI 인자를 assign_vehicle 서비스에 전달하고 등록·할당의 성공 또는 실패를 출력한다.
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