"""임시 DB·임시 사진 디렉터리에서 프로필과 비밀번호 변경을 검증함."""
import json
import base64
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.db import database
from app.db.database import get_database_connection, initialize_database
from app.db.profile_repository import change_password_and_revoke_sessions, get_profile_image_name
from app.services import profile_service
from app.services.account_service import password_hash
from app.api.dependencies import get_current_user
from app.main import create_app
from test_communication_logs import request


class AccountProfileTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = subprocess.check_output([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64",
            "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1",
        ])
        cls.old_hash = password_hash.hash("OriginalPassword123!")

    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.db_patch = patch.object(database, "DATABASE_PATH", root / "test.db")
        self.root_patch = patch.object(profile_service, "PROFILE_ROOT", root / "profiles")
        self.db_patch.start()
        self.root_patch.start()
        initialize_database()
        connection = get_database_connection()
        try:
            for name in ["first", "second"]:
                connection.execute("INSERT INTO users(username,password_hash,full_name) VALUES(?,?,?)", (name, self.old_hash, name))
            for index in range(2):
                connection.execute("INSERT INTO user_sessions(token_hash,user_id,expires_at) VALUES(?,1,'2099-01-01')", (f"token{index}",))
            connection.commit()
        finally:
            connection.close()
        self.user = SimpleNamespace(id=1, password_hash=self.old_hash, username="first",
            full_name="첫 사용자", role="USER", is_active=True, created_at="2026-10-07")

    def tearDown(self):
        self.root_patch.stop()
        self.db_patch.stop()
        self.directory.cleanup()

    def test_image_validation_and_own_image(self):
        for codec in ["mjpeg"]:
            converted = subprocess.check_output([
                "ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1",
                "-c:v", codec, "-f", "image2pipe", "pipe:1",
            ], input=self.image)
            self.assertTrue(profile_service.normalize_profile_image(converted).startswith(b"\x89PNG"))
        # 서버 FFmpeg는 WebP 인코더 없이 디코더만 있어도 업로드를 처리할 수 있음.
        tiny_webp = base64.b64decode("UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA")
        self.assertTrue(profile_service.normalize_profile_image(tiny_webp).startswith(b"\x89PNG"))
        first = profile_service.store_profile_image(1, self.image)
        second = profile_service.store_profile_image(2, self.image)
        self.assertNotEqual(first, second)
        self.assertEqual(get_profile_image_name(1), first)
        self.assertEqual(profile_service.resolve_profile_image(2).name, second)
        self.assertTrue(profile_service.resolve_profile_image(1).read_bytes().startswith(b"\x89PNG"))
        for invalid in [b"", b"<svg onload='bad()'/>", b"\x89PNG\r\n\x1a\nbroken", b"x" * (profile_service.MAX_PROFILE_BYTES + 1)]:
            with self.assertRaises(ValueError):
                profile_service.normalize_profile_image(invalid)

    def test_password_checks_and_atomic_revocation(self):
        for current, new, confirmation, exception in [
            ("wrong", "NewPassword123!", "NewPassword123!", PermissionError),
            ("OriginalPassword123!", "NewPassword123!", "NotMatching123!", ValueError),
            ("OriginalPassword123!", "short", "short", ValueError),
            ("OriginalPassword123!", "OriginalPassword123!", "OriginalPassword123!", ValueError),
        ]:
            with self.assertRaises(exception):
                profile_service.change_own_password(self.user, current, new, confirmation)
        profile_service.change_own_password(self.user, "OriginalPassword123!", "NewPassword123!", "NewPassword123!")
        connection = get_database_connection()
        try:
            stored = connection.execute("SELECT password_hash FROM users WHERE id=1").fetchone()[0]
            self.assertTrue(password_hash.verify("NewPassword123!", stored))
            self.assertFalse(password_hash.verify("OriginalPassword123!", stored))
            self.assertEqual(connection.execute("SELECT count(*) FROM user_sessions WHERE revoked_at IS NULL").fetchone()[0], 0)
            self.assertNotIn("NewPassword123!", stored)
            with self.assertRaises(ValueError):
                change_password_and_revoke_sessions(1, self.old_hash, self.old_hash)
        finally:
            connection.close()

    async def test_account_and_image_require_login_and_origin(self):
        app = create_app()
        for path in ["/account", "/api/v1/auth/profile/image"]:
            self.assertEqual((await request(app, path))[0], 401)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.assertEqual((await request(app, "/account"))[0], 200)
        self.assertEqual((await request(app, "/api/v1/auth/profile/image"))[0], 200)
        body = json.dumps({"current_password": "OriginalPassword123!", "new_password": "NewPassword123!", "confirm_password": "NewPassword123!"}).encode()
        self.assertEqual((await request(app, "/api/v1/auth/password", "POST",
            headers=[(b"origin", b"https://untrusted.example"), (b"content-type", b"application/json")], body=body))[0], 403)
        self.assertEqual((await request(app, "/api/v1/auth/password", "POST",
            headers=[(b"origin", b"https://testserver"), (b"content-type", b"application/json")], body=body))[0], 200)

    async def test_real_multipart_profile_upload(self):
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: self.user
        boundary = "ProfileTestBoundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"../../face.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
            + self.image + f"\r\n--{boundary}--\r\n".encode())
        code, response = await request(app, "/api/v1/auth/profile/image", "POST",
            headers=[(b"content-type", f"multipart/form-data; boundary={boundary}".encode())], body=body)
        self.assertEqual(code, 200, response)
        self.assertTrue(json.loads(response)["updated"])
        self.assertNotIn("..", get_profile_image_name(1))
