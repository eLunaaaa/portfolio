from getpass import getpass
from pathlib import Path
import sqlite3

from pwdlib import PasswordHash


DATABASE_PATH = Path(
    "storage/database/ace.db"
)


def open_database() -> sqlite3.Connection:
    """
    증거 서버 SQLite 데이터베이스를 열어 반환함.

    이 명령은 backend 폴더에서 실행한다고 가정함.
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
    새 비밀번호를 두 번 입력받고
    기본적인 비밀번호 조건을 검사함.
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


def find_user(
    connection: sqlite3.Connection,
    username: str,
) -> sqlite3.Row:
    """
    username으로 사용자를 찾고
    일반 사용자 계정인지 검사함.
    """

    account = connection.execute(
        """
        SELECT
            id,
            username,
            full_name,
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

    if account["role"] != "USER":
        raise ValueError(
            f"{username} 계정은 일반 사용자가 아님. "
            f"현재 역할: {account['role']}"
        )

    if not account["is_active"]:
        raise ValueError(
            f"{username} 사용자 계정이 비활성 상태임."
        )

    return account


def hash_password(
    plain_password: str,
) -> str:
    """
    평문 비밀번호를 Argon2 기반 해시로 변환함.
    """

    password_hasher = (
        PasswordHash.recommended()
    )

    return password_hasher.hash(
        plain_password
    )


def update_user_password(
    connection: sqlite3.Connection,
    user_id: int,
    new_password_hash: str,
) -> None:
    """
    사용자의 비밀번호 해시를 변경함.
    """

    cursor = connection.execute(
        """
        UPDATE users
        SET password_hash = ?
        WHERE id = ?
          AND role = 'USER'
        """,
        (
            new_password_hash,
            user_id,
        ),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "사용자 비밀번호를 변경하지 못함."
        )


def revoke_user_sessions(
    connection: sqlite3.Connection,
    user_id: int,
) -> int:
    """
    해당 사용자의 기존 로그인 세션을 모두 만료시킴.

    반환값은 만료시킨 세션 개수임.
    """

    cursor = connection.execute(
        """
        UPDATE user_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
          AND revoked_at IS NULL
        """,
        (user_id,),
    )

    return cursor.rowcount


def main() -> None:
    """
    사용자 아이디를 입력받고
    비밀번호 재설정 전체 과정을 실행함.
    """

    username = input(
        "비밀번호를 변경할 사용자 아이디: "
    ).strip()

    if not username:
        raise ValueError(
            "사용자 아이디가 비어 있음."
        )

    connection = open_database()

    try:
        user = find_user(
            connection,
            username,
        )

        print()
        print("변경 대상 계정")
        print(f"아이디: {user['username']}")
        print(
            f"이름: {user['full_name'] or '-'}"
        )
        print(f"역할: {user['role']}")
        print()

        new_password = read_new_password()

        new_password_hash = hash_password(
            new_password
        )

        update_user_password(
            connection,
            user["id"],
            new_password_hash,
        )

        revoked_session_count = (
            revoke_user_sessions(
                connection,
                user["id"],
            )
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    print()
    print(
        f"{username} 사용자 비밀번호를 변경함."
    )

    print(
        "만료시킨 기존 로그인 세션: "
        f"{revoked_session_count}개"
    )

    print(
        "사용자는 새 비밀번호로 "
        "다시 로그인해야 함."
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
        print(
            f"비밀번호 변경 실패: {error}"
        )
        raise SystemExit(1)
