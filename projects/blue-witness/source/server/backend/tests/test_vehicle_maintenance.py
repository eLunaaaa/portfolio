from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.db import database as database_module
from app.db.database import (
    get_database_connection,
    initialize_database,
)
from app.services.node_auth_service import (
    authenticate_node,
    provision_node,
)
from app.services.vehicle_maintenance_service import (
    change_plate,
    change_status,
    replace_node,
)


class VehicleMaintenanceTestCase(unittest.TestCase):
    """차량번호 변경, 비활성화, Pi 교체 정책을 검증함."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.original_database_path = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = (
            Path(self.temporary_directory.name) / "maintenance.db"
        )
        initialize_database()

        connection = get_database_connection()
        try:
            connection.execute(
                """
                INSERT INTO users (
                    username, password_hash, full_name, role
                )
                VALUES ('admin', 'test-hash', '관리자', 'ADMIN')
                """
            )
            self.admin_id = int(
                connection.execute(
                    "SELECT id FROM users WHERE username = 'admin'"
                ).fetchone()["id"]
            )
            connection.execute(
                """
                INSERT INTO vehicles (
                    vehicle_id, license_plate, display_name,
                    vehicle_model, icon_key, status
                )
                VALUES (
                    'CAR_TEST', 'TEST-1000', '시험 차량',
                    '시험 차종', 'sedan', 'ACTIVE'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        database_module.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_plate_change_is_audited(self) -> None:
        result = change_plate(
            vehicle_id="CAR_TEST",
            new_license_plate="12가 3456",
            reason="번호판 재발급",
            admin_user_id=self.admin_id,
        )

        self.assertEqual(result.license_plate, "12가3456")

        connection = get_database_connection()
        try:
            history = connection.execute(
                "SELECT * FROM vehicle_plate_history"
            ).fetchone()
            self.assertEqual(history["old_license_plate"], "TEST-1000")
            self.assertEqual(history["new_license_plate"], "12가3456")
        finally:
            connection.close()

    def test_inactive_vehicle_disables_node(self) -> None:
        provisioned = provision_node("NODE_OLD", "CAR_TEST")

        change_status("CAR_TEST", "RETIRED")

        with self.assertRaises(PermissionError):
            authenticate_node("NODE_OLD", provisioned.api_key)

    def test_node_replacement_invalidates_old_key(self) -> None:
        old = provision_node("NODE_OLD", "CAR_TEST")

        new = replace_node(
            vehicle_id="CAR_TEST",
            new_node_id="NODE_NEW",
            reason="고장 보드 교체",
            admin_user_id=self.admin_id,
        )

        with self.assertRaises(PermissionError):
            authenticate_node("NODE_OLD", old.api_key)

        authenticated = authenticate_node("NODE_NEW", new.api_key)
        self.assertEqual(authenticated.vehicle_id, "CAR_TEST")


if __name__ == "__main__":
    unittest.main()
