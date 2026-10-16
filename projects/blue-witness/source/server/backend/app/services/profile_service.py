"""프로필 이미지를 제한된 크기로 재인코딩하고 기존 Argon2 인증을 재사용함."""
from pathlib import Path
import shutil
import subprocess
import uuid

from app.db.profile_repository import (
    change_password_and_revoke_sessions, get_profile_image_name, save_profile_image_name,
)
from app.services.account_service import password_hash
from app.services.password_policy import validate_password_policy

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = BACKEND_ROOT / "storage" / "profiles"
DEFAULT_PROFILE = BACKEND_ROOT / "frontend" / "static" / "images" / "profile-default.png"
FALLBACK_PROFILE = DEFAULT_PROFILE.with_suffix(".svg")
MAX_PROFILE_BYTES = 5 * 1024 * 1024


def resolve_profile_image(user_id: int) -> Path:
    name = get_profile_image_name(user_id)
    if name and Path(name).name == name:
        path = PROFILE_ROOT / name
        if path.is_file():
            return path
    return DEFAULT_PROFILE if DEFAULT_PROFILE.is_file() else FALLBACK_PROFILE


def normalize_profile_image(data: bytes) -> bytes:
    """파일명/MIME은 신뢰하지 않으며 이미지 디코더를 강제하고 EXIF를 제거함."""
    if not data or len(data) > MAX_PROFILE_BYTES:
        raise ValueError("사진은 5MB 이하로 선택해주세요.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        codec = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        codec = "mjpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        codec = "webp"
    else:
        raise ValueError("PNG, JPG, WEBP 사진만 업로드할 수 있습니다.")
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("프로필 사진 처리에 필요한 FFmpeg가 없습니다.")
    try:
        result = subprocess.run([
            executable, "-v", "error", "-protocol_whitelist", "pipe",
            "-f", "image2pipe", "-c:v", codec, "-max_pixels", "16777216",
            "-i", "pipe:0", "-frames:v", "1", "-map_metadata", "-1",
            "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
            "-c:v", "png", "-f", "image2pipe", "pipe:1",
        ], input=data, capture_output=True, timeout=15, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError("사진 처리 시간이 초과되었습니다. 작은 사진을 선택해주세요.") from error
    if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("사진을 읽을 수 없습니다. 1,600만 화소 이하의 정상 사진을 선택해주세요.")
    return result.stdout


def store_profile_image(user_id: int, data: bytes) -> str:
    image = normalize_profile_image(data)
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    name = f"{user_id}_{uuid.uuid4().hex}.png"
    path = PROFILE_ROOT / name
    path.write_bytes(image)
    try:
        save_profile_image_name(user_id, name)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return name


def change_own_password(user, current: str, new: str, confirmation: str) -> None:
    """현재 비밀번호 확인 후 원문을 저장하지 않고 모든 기기의 로그인 세션을 폐기함."""
    if not password_hash.verify(current, user.password_hash):
        raise PermissionError("현재 비밀번호가 올바르지 않습니다.")
    validate_password_policy(new)
    if new != confirmation:
        raise ValueError("새 비밀번호 확인이 일치하지 않습니다.")
    if new == current:
        raise ValueError("현재 비밀번호와 다른 비밀번호를 입력해주세요.")
    change_password_and_revoke_sessions(user.id, user.password_hash, password_hash.hash(new))
