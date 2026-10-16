"""현재 계정의 사진 경로와 비밀번호 변경을 트랜잭션으로 관리함."""
from app.db.database import get_database_connection
from contextlib import closing


def get_profile_image_name(user_id: int) -> str | None:
    with closing(get_database_connection()) as connection, connection:
        row = connection.execute(
            "SELECT image_name FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["image_name"] if row else None


def save_profile_image_name(user_id: int, image_name: str) -> None:
    with closing(get_database_connection()) as connection, connection:
        connection.execute(
            "INSERT INTO user_profiles(user_id, image_name) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET image_name=excluded.image_name, "
            "updated_at=CURRENT_TIMESTAMP", (user_id, image_name),
        )


def change_password_and_revoke_sessions(user_id: int, old_hash: str, new_hash: str) -> None:
    """검증한 해시가 여전히 같을 때만 변경하고 모든 로그인 세션을 함께 폐기함."""
    with closing(get_database_connection()) as connection, connection:
        changed = connection.execute(
            "UPDATE users SET password_hash=? WHERE id=? AND password_hash=? AND is_active=1",
            (new_hash, user_id, old_hash),
        )
        if changed.rowcount != 1:
            raise ValueError("계정 정보가 변경되었습니다. 다시 로그인해주세요.")
        connection.execute(
            "UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP "
            "WHERE user_id=? AND revoked_at IS NULL", (user_id,),
        )
