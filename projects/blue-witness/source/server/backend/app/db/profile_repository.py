"""현재 계정의 사진 경로와 비밀번호 변경을 트랜잭션으로 관리함."""
from app.db.database import get_database_connection
from contextlib import closing


# 기능: 사용자의 프로필 이미지 파일명을 조회한다.
# 입력: 사용자 기본키 user_id.
# 출력: 등록된 image_name 또는 프로필 행이 없으면 None.
# 구조·이유: 매개변수 바인딩으로 조회하고 연결을 닫아 파일 경로 처리와 DB 접근을 분리한다.
def get_profile_image_name(user_id: int) -> str | None:
    with closing(get_database_connection()) as connection, connection:
        row = connection.execute(
            "SELECT image_name FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["image_name"] if row else None


# 기능: 프로필 이미지와 사용자 간 연결을 저장한다.
# 입력: 사용자 기본키 user_id, 저장을 마친 이미지 파일명 image_name.
# 출력: 반환값 없음; 실패 시 DB 예외.
# 구조·이유: INSERT ON CONFLICT로 신규 등록과 기존 파일명·수정 시각 갱신을 하나의 트랜잭션으로 처리한다.
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
