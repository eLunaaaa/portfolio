import os


def get_setting(
    name: str,
    legacy_name: str,
    default: str,
) -> str:
    """새 환경변수를 우선하고 기존 변수는 호환용으로 읽음."""

    return os.getenv(
        name,
        os.getenv(legacy_name, default),
    )
