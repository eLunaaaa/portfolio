from dataclasses import dataclass
import sqlite3

from app.db.portal_repository import (
    connect_database,
)


@dataclass(frozen=True)
class UserDeletionTarget:
    user_id: int
    username: str
    role: str


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    """
    SQLite에 특정 테이블이 존재하는지 확인함.
    """

    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def get_event_id(
    event_id: str,
) -> str | None:
    """
    삭제할 사고가 실제 DB에 존재하는지 확인함.
    """

    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT event_id
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

    if row is None:
        return None

    return row["event_id"]


def get_user_deletion_target(
    user_id: int,
) -> UserDeletionTarget | None:
    """
    삭제 대상 사용자의 아이디와 역할을 조회함.
    """

    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                username,
                role
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        return None

    return UserDeletionTarget(
        user_id=int(row["id"]),
        username=row["username"],
        role=row["role"],
    )


def delete_event_records(
    event_id: str,
) -> dict[str, int]:
    """
    하나의 사고에 연결된 DB 데이터를
    트랜잭션으로 삭제함.
    """

    deleted_counts: dict[str, int] = {}

    connection = connect_database()

    try:
        if table_exists(
            connection,
            "access_sessions",
        ):
            cursor = connection.execute(
                """
                DELETE FROM access_sessions
                WHERE event_id = ?
                """,
                (event_id,),
            )

            deleted_counts[
                "access_sessions"
            ] = cursor.rowcount

        if table_exists(
            connection,
            "event_access",
        ):
            cursor = connection.execute(
                """
                DELETE FROM event_access
                WHERE event_id = ?
                """,
                (event_id,),
            )

            deleted_counts[
                "event_access"
            ] = cursor.rowcount

        if table_exists(
            connection,
            "merge_jobs",
        ):
            cursor = connection.execute(
                """
                DELETE FROM merge_jobs
                WHERE event_id = ?
                """,
                (event_id,),
            )

            deleted_counts[
                "merge_jobs"
            ] = cursor.rowcount

        cursor = connection.execute(
            """
            DELETE FROM evidence
            WHERE event_id = ?
            """,
            (event_id,),
        )

        deleted_counts[
            "evidence"
        ] = cursor.rowcount

        cursor = connection.execute(
            """
            DELETE FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        )

        deleted_counts[
            "events"
        ] = cursor.rowcount

        connection.commit()

        return deleted_counts

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def delete_user_records(
    user_id: int,
) -> dict[str, int]:
    """
    일반 사용자와 로그인 세션,
    차량 배정 관계를 삭제함.

    차량 자체는 물리 노드 정보일 수 있으므로
    개별 사용자 삭제에서는 유지함.
    """

    deleted_counts: dict[str, int] = {}

    connection = connect_database()

    try:
        cursor = connection.execute(
            """
            DELETE FROM user_sessions
            WHERE user_id = ?
            """,
            (user_id,),
        )

        deleted_counts[
            "user_sessions"
        ] = cursor.rowcount

        cursor = connection.execute(
            """
            DELETE FROM user_vehicles
            WHERE user_id = ?
            """,
            (user_id,),
        )

        deleted_counts[
            "user_vehicles"
        ] = cursor.rowcount

        cursor = connection.execute(
            """
            DELETE FROM users
            WHERE id = ?
            """,
            (user_id,),
        )

        deleted_counts[
            "users"
        ] = cursor.rowcount

        connection.commit()

        return deleted_counts

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def reset_demo_records(
    preserved_admin_user_id: int,
) -> dict[str, int]:
    """
    현재 로그인한 관리자 한 명과
    DB 테이블 구조를 제외한 시연 데이터를 삭제함.
    """

    deleted_counts: dict[str, int] = {}

    connection = connect_database()

    try:
        delete_order = (
            "access_sessions",
            "event_access",
            "merge_jobs",
            "evidence",
            "events",
            "vehicle_nodes",
            "user_vehicles",
            "vehicles",
        )

        for table_name in delete_order:
            if not table_exists(
                connection,
                table_name,
            ):
                continue

            cursor = connection.execute(
                f"DELETE FROM {table_name}"
            )

            deleted_counts[
                table_name
            ] = cursor.rowcount

        cursor = connection.execute(
            """
            DELETE FROM user_sessions
            WHERE user_id != ?
            """,
            (preserved_admin_user_id,),
        )

        deleted_counts[
            "user_sessions"
        ] = cursor.rowcount

        cursor = connection.execute(
            """
            DELETE FROM users
            WHERE id != ?
            """,
            (preserved_admin_user_id,),
        )

        deleted_counts[
            "users"
        ] = cursor.rowcount

        if table_exists(
            connection,
            "system_logs",
        ):
            cursor = connection.execute(
                "DELETE FROM system_logs"
            )

            deleted_counts[
                "system_logs"
            ] = cursor.rowcount

        connection.commit()

        return deleted_counts

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
