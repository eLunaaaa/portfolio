"""포털 차량 작업은 임시 DB에서만 검증하고 실제 사고 데이터는 건드리지 않음."""

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import sqlite3
import unittest

from pydantic import ValidationError

from app.api.vehicles import edit_portal_vehicle
from app.db.account_repository import UserRecord
from app.db import database as database_module
from app.db.database import get_database_connection, initialize_database
from app.db.vehicle_repository import (
    create_user_vehicle,
    VehicleAlreadyRegisteredError,
    list_user_vehicles,
    unlink_user_vehicle,
    update_owned_vehicle,
    update_user_vehicle_nickname,
)
from app.schemas.vehicle import (
    VehicleCreateRequest,
    VehicleNicknameRequest,
    VehicleUpdateRequest,
)
from app.services.vehicle_operation_service import (
    VehicleOperationDeniedError,
    issue_vehicle_operation_grant,
)


class PortalVehicleManagementTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.original_path = database_module.DATABASE_PATH
        self.original_directory = database_module.DATABASE_DIRECTORY
        database_module.DATABASE_DIRECTORY = Path(self.temporary_directory.name)
        database_module.DATABASE_PATH = Path(self.temporary_directory.name) / "test.db"
        initialize_database()
        connection = get_database_connection()
        try:
            connection.execute(
                "INSERT INTO users (username, password_hash, full_name) "
                "VALUES ('owner', 'test-hash', '차주')"
            )
            self.user_id = connection.execute(
                "SELECT id FROM users WHERE username='owner'"
            ).fetchone()["id"]
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        database_module.DATABASE_PATH = self.original_path
        database_module.DATABASE_DIRECTORY = self.original_directory
        self.temporary_directory.cleanup()

    def create_vehicle(self, plate="12가3456"):
        return create_user_vehicle(
            self.user_id, plate, "현대 아이오닉 5", "처음 별명", "sedan",
            issue_vehicle_operation_grant(self.user_id, "register", None),
        )

    def grant_is_consumed(self, token):
        connection = get_database_connection()
        try:
            row = connection.execute(
                "SELECT consumed_at FROM vehicle_operation_grants WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            return row["consumed_at"] is not None
        finally:
            connection.close()

    def plate_history(self, vehicle_id):
        connection = get_database_connection()
        try:
            return connection.execute(
                "SELECT * FROM vehicle_plate_history WHERE vehicle_id=? ORDER BY id",
                (vehicle_id,),
            ).fetchall()
        finally:
            connection.close()

    def test_edit_saves_metadata_nickname_and_plate_history_together(self):
        vehicle = self.create_vehicle()
        token = issue_vehicle_operation_grant(self.user_id, "update", vehicle.vehicle_id)
        user = UserRecord(
            self.user_id, "owner", "test-hash", "차주", "USER", True, "2026-10-06"
        )
        changed = edit_portal_vehicle(
            vehicle.vehicle_id,
            VehicleUpdateRequest(
                license_plate="34나 5678", vehicle_model=" 현대 아반떼 ",
                icon_key="hyundai_avante", display_name=" 새 별명 ",
            ),
            token, user,
        )
        self.assertEqual(changed.license_plate, "34나5678")
        self.assertEqual(changed.vehicle_model, "현대 아반떼")
        self.assertEqual(changed.icon_key, "hyundai_avante")
        self.assertEqual(changed.display_name, "새 별명")
        saved = list_user_vehicles(self.user_id)[0]
        self.assertEqual(saved.license_plate, changed.license_plate)
        self.assertEqual(saved.display_name, changed.display_name)
        history = self.plate_history(vehicle.vehicle_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["old_license_plate"], "12가3456")
        self.assertEqual(history[0]["new_license_plate"], "34나5678")
        self.assertEqual(history[0]["changed_by_user_id"], self.user_id)
        self.assertTrue(history[0]["change_reason"])
        self.assertTrue(history[0]["changed_at"])
        self.assertTrue(self.grant_is_consumed(token))
        with self.assertRaises(VehicleOperationDeniedError):
            update_owned_vehicle(
                self.user_id, vehicle.vehicle_id, "34나5678", "현대 아반떼",
                "hyundai_avante", token, display_name="다시 저장",
            )

    def test_omitted_nickname_and_unchanged_plate_preserve_existing_values(self):
        vehicle = self.create_vehicle()
        update_user_vehicle_nickname(self.user_id, vehicle.vehicle_id, "내 별명")
        payload = VehicleUpdateRequest(
            license_plate="12가 3456", vehicle_model="현대 아반떼", icon_key="sedan"
        )
        token = issue_vehicle_operation_grant(self.user_id, "update", vehicle.vehicle_id)
        changed = update_owned_vehicle(
            self.user_id, vehicle.vehicle_id, payload.license_plate,
            payload.vehicle_model, payload.icon_key, token,
            display_name=payload.display_name,
        )
        self.assertEqual(changed.display_name, "내 별명")
        self.assertEqual(changed.vehicle_model, "현대 아반떼")
        self.assertEqual(self.plate_history(vehicle.vehicle_id), [])

    def test_failed_nickname_or_history_write_rolls_back_and_allows_retry(self):
        for index, (table, operation) in enumerate((
            ("user_vehicles", "UPDATE OF display_name"),
            ("vehicle_plate_history", "INSERT"),
        )):
            with self.subTest(table=table):
                vehicle = self.create_vehicle(f"TEST-{index}")
                token = issue_vehicle_operation_grant(
                    self.user_id, "update", vehicle.vehicle_id
                )
                connection = get_database_connection()
                try:
                    connection.execute(
                        f"CREATE TRIGGER fail_write BEFORE {operation} ON {table} "
                        "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END"
                    )
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(sqlite3.IntegrityError):
                    update_owned_vehicle(
                        self.user_id, vehicle.vehicle_id, f"NEW-{index}", "새 차종",
                        "suv", token, display_name="새 별명",
                    )
                saved = next(
                    item for item in list_user_vehicles(self.user_id)
                    if item.vehicle_id == vehicle.vehicle_id
                )
                self.assertEqual(saved, vehicle)
                self.assertEqual(self.plate_history(vehicle.vehicle_id), [])
                self.assertFalse(self.grant_is_consumed(token))
                connection = get_database_connection()
                try:
                    connection.execute("DROP TRIGGER fail_write")
                    connection.commit()
                finally:
                    connection.close()
                retried = update_owned_vehicle(
                    self.user_id, vehicle.vehicle_id, f"NEW-{index}", "새 차종",
                    "suv", token, display_name="새 별명",
                )
                self.assertEqual(retried.license_plate, f"NEW-{index}")
                self.assertEqual(retried.display_name, "새 별명")
                self.assertEqual(len(self.plate_history(vehicle.vehicle_id)), 1)
                self.assertTrue(self.grant_is_consumed(token))

    def test_duplicate_plate_keeps_grant_and_existing_vehicle_for_retry(self):
        vehicle = self.create_vehicle()
        self.create_vehicle("34나5678")
        token = issue_vehicle_operation_grant(self.user_id, "update", vehicle.vehicle_id)
        with self.assertRaises(VehicleAlreadyRegisteredError):
            update_owned_vehicle(
                self.user_id, vehicle.vehicle_id, "34나5678", "새 차종", "suv",
                token, display_name="새 별명",
            )
        self.assertFalse(self.grant_is_consumed(token))
        self.assertEqual(self.plate_history(vehicle.vehicle_id), [])
        changed = update_owned_vehicle(
            self.user_id, vehicle.vehicle_id, "56다7890", "새 차종", "suv",
            token, display_name="새 별명",
        )
        self.assertEqual(changed.license_plate, "56다7890")
        self.assertTrue(self.grant_is_consumed(token))

    def test_register_edit_photo_nickname_and_unlink(self):
        with self.assertRaises(VehicleOperationDeniedError):
            create_user_vehicle(
                self.user_id, "12가3456", "현대 아이오닉 5", "처음 별명",
                "hyundai_ioniq_five", "",
            )

        token = issue_vehicle_operation_grant(self.user_id, "register", None)
        vehicle = create_user_vehicle(
            self.user_id, "12가3456", "현대 아이오닉 5", "처음 별명",
            "hyundai_ioniq_five", token,
        )
        with self.assertRaises(VehicleOperationDeniedError):
            create_user_vehicle(
                self.user_id, "13가3456", "시험 차종", "다른 차",
                "sedan", token,
            )

        nickname = update_user_vehicle_nickname(
            self.user_id, vehicle.vehicle_id, "내 차"
        )
        self.assertEqual(nickname.display_name, "내 차")

        token = issue_vehicle_operation_grant(
            self.user_id, "update", vehicle.vehicle_id
        )
        changed = update_owned_vehicle(
            self.user_id, vehicle.vehicle_id, "12가3456", "현대 아반떼",
            "hyundai_avante", token,
        )
        self.assertEqual(changed.icon_key, "hyundai_avante")
        self.assertEqual(changed.display_name, "내 차")
        self.assertEqual(list_user_vehicles(self.user_id)[0].icon_key, "hyundai_avante")

        token = issue_vehicle_operation_grant(
            self.user_id, "delete", vehicle.vehicle_id
        )
        unlink_user_vehicle(self.user_id, vehicle.vehicle_id, token)
        self.assertEqual(list_user_vehicles(self.user_id), [])
        connection = get_database_connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM vehicles WHERE vehicle_id=?",
                    (vehicle.vehicle_id,),
                ).fetchone()[0], 1,
            )
        finally:
            connection.close()


class VehicleInputValidationTest(unittest.TestCase):
    def test_registration_and_update_reject_the_same_invalid_fields(self):
        values = {
            "license_plate": "12가3456", "vehicle_model": "현대 아이오닉 5",
            "icon_key": "sedan", "display_name": "내 차",
        }
        for field, invalid_value in (
            ("license_plate", "!!"), ("license_plate", " "),
            ("vehicle_model", "아이오닉\x00 5"), ("vehicle_model", "  "),
            ("display_name", "내\n차"), ("display_name", "  "),
            ("icon_key", "../sedan"), ("icon_key", ""),
        ):
            for schema in (VehicleCreateRequest, VehicleUpdateRequest):
                with self.subTest(field=field, schema=schema.__name__):
                    with self.assertRaises(ValidationError):
                        schema(**{**values, field: invalid_value})

    def test_nickname_only_request_uses_the_same_text_validation(self):
        self.assertEqual(VehicleNicknameRequest(display_name=" 내 차 ").display_name, "내 차")
        for invalid_value in (" ", "내\x00차", "내\n차"):
            with self.subTest(value=invalid_value):
                with self.assertRaises(ValidationError):
                    VehicleNicknameRequest(display_name=invalid_value)


if __name__ == "__main__":
    unittest.main()
