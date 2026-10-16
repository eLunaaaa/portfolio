from getpass import getpass
from pathlib import Path
import sqlite3
import sys

from pwdlib import PasswordHash


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.database import DATABASE_PATH


def open_database() -> sqlite3.Connection:
    """
    증거 서버 SQLite 데이터베이스를 열고,
    조회 결과를 컬럼 이름으로 읽을 수 있게 설정함.
    """

    if not DATABASE_PATH.exists():
        raise RuntimeError(
            "데이터베이스 파일을 찾을 수 없음: "
            f"{DATABASE_PATH.resolve()}"
        )

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = sqlite3.Row

    return connection


def read_new_password() -> str:
    """
    터미널에서 새 비밀번호를 두 번 입력받고
    기본 형식을 검사함.
    """

    new_password = getpass(
        "새 비밀번호: "
    )

    confirm_password = getpass(
        "새 비밀번호 확인: "
    )

    if new_password != confirm_password:
        raise ValueError(
            "입력한 두 비밀번호가 서로 다름."
        )

    if len(new_password) < 8:
        raise ValueError(
            "비밀번호는 최소 8자 이상이어야 함."
        )

    return new_password


def find_admin(
    connection: sqlite3.Connection,
    username: str,
) -> sqlite3.Row:
    """
    입력한 username의 사용자를 조회하고
    활성화된 관리자 계정인지 검사함.
    """

    account = connection.execute(
        """
        SELECT
            id,
            username,
            role,
            is_active
        FROM users
        WHERE username = ? COLLATE NOCASE
        """,
        (username,),
    ).fetchone()

    if account is None:
        raise ValueError(
            f"사용자를 찾을 수 없음: {username}"
        )

    if account["role"] != "ADMIN":
        raise ValueError(
            f"{username} 계정은 관리자가 아님. "
            f"현재 역할: {account['role']}"
        )

    if not account["is_active"]:
        raise ValueError(
            f"{username} 관리자 계정이 비활성 상태임."
        )

    return account


def update_admin_password(
    connection: sqlite3.Connection,
    user_id: int,
    new_password: str,
) -> None:
    """
    새 비밀번호를 Argon2 해시로 변환하여 저장하고,
    기존 로그인 세션을 모두 만료시킴.
    """

    password_hasher = (
        PasswordHash.recommended()
    )

    new_password_hash = (
        password_hasher.hash(
            new_password
        )
    )

    connection.execute(
        """
        UPDATE users
        SET password_hash = ?
        WHERE id = ?
        """,
        (
            new_password_hash,
            user_id,
        ),
    )

    connection.execute(
        """
        UPDATE user_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
          AND revoked_at IS NULL
        """,
        (user_id,),
    )

    connection.commit()


def main() -> None:
    """
    관리자 아이디와 새 비밀번호를 입력받아
    전체 재설정 과정을 실행함.
    """

    username = input(
        "비밀번호를 변경할 관리자 아이디: "
    ).strip()

    if not username:
        raise ValueError(
            "관리자 아이디가 비어 있음."
        )

    new_password = read_new_password()

    connection = open_database()

    try:
        account = find_admin(
            connection,
            username,
        )

        update_admin_password(
            connection,
            account["id"],
            new_password,
        )

    finally:
        connection.close()

    print()
    print(
        f"{username} 관리자 비밀번호를 변경함."
    )

    print(
        "기존 관리자 로그인 세션도 "
        "모두 만료시킴."
    )


if __name__ == "__main__":
    try:
        main()

    except (
        RuntimeError,
        ValueError,
        sqlite3.Error,
    ) as error:
        print()
        print(f"비밀번호 변경 실패: {error}")
        raise SystemExit(1)
