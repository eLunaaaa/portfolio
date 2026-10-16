from dataclasses import dataclass
import secrets
import sqlite3

from app.db.database import (
    get_database_connection,
)
from app.services.vehicle_operation_service import (
    VehicleOperationDeniedError,
    consume_vehicle_operation_grant,
    require_owned_vehicle,
)


# 직접 입력 차량에 사용하는 일반 아이콘임.
VALID_GENERIC_ICON_KEYS = {
    "sedan",
    "suv",
    "van",
    "pickup",
    "truck",
}


# 현대 차량 사진과 연결되는 키임.
#
# 실제 이미지 파일 경로는 프론트엔드에서
# 고정된 목록으로 변환함.
VALID_HYUNDAI_PHOTO_KEYS = {
    "hyundai_solati",
    "hyundai_porter_two",
    "hyundai_nexo",
    "hyundai_ioniq_five",
    "hyundai_staria",
    "hyundai_palisade",
    "hyundai_santafe",
    "hyundai_tucson",
    "hyundai_kona_electric",
    "hyundai_kona",
    "hyundai_venue",
    "hyundai_casper",
    "hyundai_ioniq_six",
    "hyundai_grandeur",
    "hyundai_sonata",
    "hyundai_avante",
}


# 서버에서 허용하는 모든 화면 표시 키임.
VALID_ICON_KEYS = (
    VALID_GENERIC_ICON_KEYS
    | VALID_HYUNDAI_PHOTO_KEYS
)


@dataclass(frozen=True)
class VehicleRecord:
    """
    DB에서 조회한 차량 한 대를 표현함.

    icon_key에는 일반 아이콘 또는
    현대차 사진 키가 저장될 수 있음.

    예:
        sedan
        hyundai_ioniq_five
    """

    vehicle_id: str
    license_plate: str
    vehicle_model: str | None
    display_name: str | None
    icon_key: str
    status: str


class VehicleAlreadyRegisteredError(
    ValueError
):
    """
    이미 등록된 차량번호를 다시 등록했을 때
    발생시키는 예외임.
    """


def normalize_license_plate(
    license_plate: str,
) -> str:
    """
    차량번호 비교 형식을 통일함.

    예:
        "12가 3456"
        "12가3456"

    두 입력을 모두 "12가3456"으로 처리함.
    """

    return "".join(
        license_plate.split()
    ).upper()


def generate_vehicle_id(
    connection: sqlite3.Connection,
) -> str:
    """
    서버 내부에서 사용할 차량 ID를 생성함.

    차량번호와 IP 주소는 변경될 수 있으므로
    내부 식별자로 사용하지 않음.

    생성 예:
        CAR_A1B2C3D4
    """

    for _ in range(20):
        candidate = (
            "CAR_"
            + secrets.token_hex(4).upper()
        )

        existing = connection.execute(
            """
            SELECT 1
            FROM vehicles
            WHERE vehicle_id = ?
            """,
            (candidate,),
        ).fetchone()

        if existing is None:
            return candidate

    raise RuntimeError(
        "고유한 vehicle_id 생성에 실패함."
    )


def row_to_vehicle_record(
    row: sqlite3.Row,
) -> VehicleRecord:
    """
    SQLite Row를 VehicleRecord로 변환함.

    기존 차량에서 icon_key가 비어 있다면
    기본 승용차 아이콘으로 처리함.
    """

    return VehicleRecord(
        vehicle_id=row["vehicle_id"],
        license_plate=row["license_plate"],
        vehicle_model=row["vehicle_model"],
        display_name=row["display_name"],
        icon_key=(
            row["icon_key"]
            or "sedan"
        ),
        status=(
            row["status"]
            if "status" in row.keys()
            else "ACTIVE"
        ),
    )


def list_user_vehicles(
    user_id: int,
) -> list[VehicleRecord]:
    """
    특정 사용자에게 등록된 차량을 조회함.

    user_vehicles:
        사용자와 차량의 소유 관계임.

    vehicles:
        차량번호, 차종, 별명,
        아이콘 또는 사진 키 정보임.
    """

    connection = get_database_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                vehicles.vehicle_id,
                vehicles.license_plate,
                vehicles.vehicle_model,
                COALESCE(user_vehicles.display_name, vehicles.display_name)
                    AS display_name,
                vehicles.icon_key,
                vehicles.status
            FROM user_vehicles
            INNER JOIN vehicles
                ON vehicles.vehicle_id
                   = user_vehicles.vehicle_id
            WHERE user_vehicles.user_id = ?
            ORDER BY
                vehicles.license_plate ASC
            """,
            (user_id,),
        ).fetchall()

        return [
            row_to_vehicle_record(row)
            for row in rows
        ]

    finally:
        connection.close()


def create_user_vehicle(
    user_id: int,
    license_plate: str,
    vehicle_model: str,
    display_name: str,
    icon_key: str,
    verification_token: str,
) -> VehicleRecord:
    """
    새로운 차량을 등록하고 로그인 사용자와 연결함.

    하나의 Transaction에서 다음 작업을 처리함.

    1. 입력값을 정규화하고 검증함.
    2. vehicles 테이블에 차량을 추가함.
    3. user_vehicles에 사용자 소유 관계를 추가함.

    둘 중 하나라도 실패하면 전체 작업을
    rollback하여 일부 데이터만 저장되는 것을
    방지함.
    """

    normalized_plate = (
        normalize_license_plate(
            license_plate
        )
    )

    normalized_model = (
        vehicle_model.strip()
    )

    normalized_display_name = (
        display_name.strip()
    )

    normalized_icon_key = (
        icon_key.strip().lower()
    )

    if not normalized_plate:
        raise ValueError(
            "차량번호가 필요함."
        )

    if not normalized_model:
        raise ValueError(
            "차종이 필요함."
        )

    if not normalized_display_name:
        raise ValueError(
            "차량 별명이 필요함."
        )

    if (
        normalized_icon_key
        not in VALID_ICON_KEYS
    ):
        raise ValueError(
            "지원하지 않는 차량 사진 또는 아이콘임."
        )

    connection = get_database_connection()

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )
        consume_vehicle_operation_grant(
            connection, user_id, "register", None, verification_token
        )

        existing_vehicle = (
            connection.execute(
                """
                SELECT vehicle_id
                FROM vehicles
                WHERE REPLACE(
                    UPPER(license_plate),
                    ' ',
                    ''
                ) = ?
                """,
                (normalized_plate,),
            ).fetchone()
        )

        if existing_vehicle is not None:
            raise (
                VehicleAlreadyRegisteredError(
                    "이미 등록된 차량번호임."
                )
            )

        vehicle_id = generate_vehicle_id(
            connection
        )

        connection.execute(
            """
            INSERT INTO vehicles (
                vehicle_id,
                license_plate,
                vehicle_model,
                display_name,
                icon_key,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                vehicle_id,
                normalized_plate,
                normalized_model,
                normalized_display_name,
                normalized_icon_key,
            ),
        )

        connection.execute(
            """
            INSERT INTO user_vehicles (
                user_id,
                vehicle_id,
                display_name
            )
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                vehicle_id,
                normalized_display_name,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT
                vehicle_id,
                license_plate,
                vehicle_model,
                display_name,
                icon_key,
                status
            FROM vehicles
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "등록한 차량을 다시 조회할 수 없음."
            )

        return row_to_vehicle_record(
            row
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def update_user_vehicle_nickname(
    user_id: int, vehicle_id: str, display_name: str
) -> VehicleRecord:
    """별명만 현재 사용자 연결에 저장함. 별도 확인 토큰은 필요 없음."""
    cleaned = display_name.strip()
    if not cleaned or len(cleaned) > 80 or any(ord(c) < 32 for c in cleaned):
        raise ValueError("차량 별명은 1~80자의 일반 문자여야 함.")
    connection = get_database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        require_owned_vehicle(connection, user_id, vehicle_id)
        connection.execute(
            "UPDATE user_vehicles SET display_name=? WHERE user_id=? AND vehicle_id=?",
            (cleaned, user_id, vehicle_id),
        )
        row = connection.execute(
            """SELECT v.vehicle_id, v.license_plate, v.vehicle_model,
                      uv.display_name, v.icon_key, v.status
               FROM vehicles v JOIN user_vehicles uv ON uv.vehicle_id=v.vehicle_id
               WHERE uv.user_id=? AND v.vehicle_id=?""",
            (user_id, vehicle_id),
        ).fetchone()
        connection.commit()
        return row_to_vehicle_record(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_owned_vehicle(
    user_id: int,
    vehicle_id: str,
    license_plate: str,
    vehicle_model: str,
    icon_key: str,
    verification_token: str,
    display_name: str | None = None,
) -> VehicleRecord:
    """차량 정보·별명·번호 변경 이력·확인 권한을 한 트랜잭션으로 저장함."""
    plate = normalize_license_plate(license_plate)
    model = vehicle_model.strip()
    key = icon_key.strip().lower()
    if not plate or not model or key not in VALID_ICON_KEYS:
        raise ValueError("차량번호·차종·사진 정보가 올바르지 않음.")
    nickname = display_name.strip() if display_name is not None else None
    if nickname is not None and (
        not nickname or len(nickname) > 80 or any(ord(c) < 32 for c in nickname)
    ):
        raise ValueError("차량 별명은 1~80자의 일반 문자여야 함.")
    connection = get_database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        require_owned_vehicle(connection, user_id, vehicle_id)
        owner_count = connection.execute(
            "SELECT COUNT(*) FROM user_vehicles WHERE vehicle_id=?", (vehicle_id,)
        ).fetchone()[0]
        if owner_count != 1:
            raise VehicleOperationDeniedError(
                "공동 소유 차량 정보는 관리자에게 변경을 요청해주세요."
            )
        consume_vehicle_operation_grant(
            connection, user_id, "update", vehicle_id, verification_token
        )
        duplicate = connection.execute(
            """SELECT vehicle_id FROM vehicles
               WHERE REPLACE(UPPER(license_plate), ' ', '')=? AND vehicle_id<>?""",
            (plate, vehicle_id),
        ).fetchone()
        if duplicate is not None:
            raise VehicleAlreadyRegisteredError("이미 등록된 차량번호임.")
        old_plate = connection.execute(
            "SELECT license_plate FROM vehicles WHERE vehicle_id=?", (vehicle_id,)
        ).fetchone()["license_plate"]
        connection.execute(
            """UPDATE vehicles SET license_plate=?, vehicle_model=?, icon_key=?
               WHERE vehicle_id=?""",
            (plate, model, key, vehicle_id),
        )
        if old_plate != plate:
            connection.execute(
                """INSERT INTO vehicle_plate_history (
                       vehicle_id, old_license_plate, new_license_plate,
                       changed_by_user_id, change_reason
                   ) VALUES (?, ?, ?, ?, ?)""",
                (vehicle_id, old_plate, plate, user_id, "차주가 차량 정보 수정"),
            )
        if nickname is not None:
            connection.execute(
                "UPDATE user_vehicles SET display_name=? WHERE user_id=? AND vehicle_id=?",
                (nickname, user_id, vehicle_id),
            )
        row = connection.execute(
            """SELECT v.vehicle_id, v.license_plate, v.vehicle_model,
                      COALESCE(uv.display_name, v.display_name) AS display_name,
                      v.icon_key, v.status
               FROM vehicles v JOIN user_vehicles uv ON uv.vehicle_id=v.vehicle_id
               WHERE uv.user_id=? AND v.vehicle_id=?""",
            (user_id, vehicle_id),
        ).fetchone()
        connection.commit()
        return row_to_vehicle_record(row)
    except sqlite3.IntegrityError as error:
        connection.rollback()
        if error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
            raise VehicleAlreadyRegisteredError("이미 등록된 차량번호임.") from error
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def unlink_user_vehicle(
    user_id: int, vehicle_id: str, verification_token: str
) -> None:
    """사용자 연결만 해제하며 차량·Pi·사고 기록은 보존함."""
    connection = get_database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        require_owned_vehicle(connection, user_id, vehicle_id)
        consume_vehicle_operation_grant(
            connection, user_id, "delete", vehicle_id, verification_token
        )
        connection.execute(
            "DELETE FROM user_vehicles WHERE user_id=? AND vehicle_id=?",
            (user_id, vehicle_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
