import argparse
import getpass
import sys

from app.services.account_service import (
    register_account,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="증거 서버 관리자 계정을 생성함."
    )

    parser.add_argument(
        "username",
        help="관리자 로그인 아이디",
    )

    parser.add_argument(
        "--name",
        required=True,
        help="관리자 표시 이름",
    )

    arguments = parser.parse_args()

    password = getpass.getpass(
        "관리자 비밀번호: "
    )
    password_confirm = getpass.getpass(
        "비밀번호 확인: "
    )

    if password != password_confirm:
        print("비밀번호가 일치하지 않음.")
        sys.exit(1)

    try:
        user = register_account(
            username=arguments.username,
            password=password,
            full_name=arguments.name,
            role="ADMIN",
        )

    except Exception as error:
        print(f"관리자 생성 실패: {error}")
        sys.exit(1)

    print()
    print("관리자 생성 완료")
    print(f"- ID: {user.username}")
    print(f"- 이름: {user.full_name}")
    print(f"- 역할: {user.role}")


if __name__ == "__main__":
    main()
