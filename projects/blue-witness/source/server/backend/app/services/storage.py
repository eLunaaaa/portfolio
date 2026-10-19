import hashlib
import os
import re
import secrets

from datetime import datetime, timedelta, timezone

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


PROJECT_ROOT = (
    Path(__file__).resolve().parents[2]
)

ORIGINALS_DIRECTORY = (
    PROJECT_ROOT
    / "storage"
    / "originals"
)

MAX_UPLOAD_SIZE = (
    100 * 1024 * 1024
)

CHUNK_SIZE = (
    1024 * 1024
)

SHA256_PATTERN = re.compile(
    r"^[0-9a-f]{64}$"
)

KST = timezone(timedelta(hours=9))


class FileTooLargeError(Exception):
    pass


class EmptyFileError(Exception):
    pass


class StoredFileAlreadyExistsError(
    Exception
):
    pass


class FileChecksumMismatchError(
    Exception
):
    pass


class StoredFileMissingError(
    Exception
):
    pass


@dataclass(frozen=True)
class StoredFile:
    """
    서버에 저장된 영상 파일 정보임.

    reused_existing:
        False이면 이번 요청에서 새로 저장함.
        True이면 기존 동일 파일을 재사용함.
    """

    absolute_path: Path
    relative_path: str

    size_bytes: int
    sha256: str

    reused_existing: bool


def normalize_sha256(
    value: str,
) -> str:
    """
    SHA-256 문자열을 소문자로 통일하고
    64자리 16진수인지 검사함.
    """

    normalized = (
        value.strip().lower()
    )

    if not SHA256_PATTERN.fullmatch(
        normalized
    ):
        raise ValueError(
            "X-Evidence-Content-SHA256은 "
            "64자리 SHA-256이어야 함."
        )

    return normalized


def calculate_file_sha256(
    file_path: Path,
) -> str:
    """
    디스크에 저장된 파일의 SHA-256을 계산함.

    파일 전체를 메모리에 올리지 않고
    1MB 단위로 읽음.
    """

    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        while True:
            chunk = input_file.read(
                CHUNK_SIZE
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def build_video_path(
    event_id: str,
    vehicle_id: str,
    event_time_ns: int | None = None,
) -> Path:
    """
    사고 발생 날짜별 최종 영상 경로를 생성함.

    신규 저장:
        originals/YYYY/MM/DD/event_id/vehicle_id/clip.mp4

    기존 조회:
        event_time_ns가 없으면 과거 경로도 계속 지원함.
    """

    if event_time_ns is None:
        return (
            ORIGINALS_DIRECTORY
            / event_id
            / vehicle_id
            / "clip.mp4"
        )

    event_datetime = datetime.fromtimestamp(
        event_time_ns / 1_000_000_000,
        tz=timezone.utc,
    ).astimezone(KST)

    return (
        ORIGINALS_DIRECTORY
        / event_datetime.strftime("%Y")
        / event_datetime.strftime("%m")
        / event_datetime.strftime("%d")
        / event_id
        / vehicle_id
        / "clip.mp4"
    )


def sync_directory_chain(directory: Path) -> None:
    """저장 경로를 검사하고 POSIX에서 부모 디렉터리를 동기화함."""
    boundary = PROJECT_ROOT.resolve()
    current = directory.resolve()
    if current != boundary and boundary not in current.parents:
        raise StoredFileMissingError("동기화 경로가 저장 루트 밖을 가리킴.")
    if os.name == "nt":
        # Windows의 파일 fsync는 각 파일 핸들에서 수행함.
        return
    while True:
        descriptor = os.open(current, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if current == boundary:
            break
        current = current.parent


# 기능: 영상 파일과 상위 디렉터리의 저장 내용을 동기화한다.
# 입력: 저장을 마친 파일의 Path.
# 출력: 반환값 없음; 파일 열기·동기화 실패는 예외.
# 구조·이유: OS별 파일 열기 권한을 맞춰 fsync하고 디렉터리 체인을 동기화해 저장 완료 응답의 내구성을 높인다.
def sync_stored_file(path: Path) -> None:
    # Windows FlushFileBuffers에는 쓰기 권한을 가진 핸들이 필요함.
    with path.open("r+b" if os.name == "nt" else "rb") as stored:
        os.fsync(stored.fileno())
    sync_directory_chain(path.parent)


def inspect_stored_mp4(
    event_id: str,
    vehicle_id: str,
    expected_sha256: str,
    stored_relative_path: str | None = None,
) -> StoredFile:
    """
    기존 clip.mp4가 실제로 존재하며
    Pi가 보낸 SHA-256과 같은지 검사함.

    일치하면 기존 파일 정보를 반환함.
    """

    normalized_expected_sha256 = (
        normalize_sha256(
            expected_sha256
        )
    )

    if stored_relative_path is None:
        final_path = build_video_path(
            event_id=event_id,
            vehicle_id=vehicle_id,
        )
    else:
        final_path = (
            PROJECT_ROOT
            / stored_relative_path
        ).resolve()

        # DB 경로를 신뢰하기 전에 storage 내부인지 확인함.
        storage_root = (
            PROJECT_ROOT / "storage"
        ).resolve()

        if storage_root not in final_path.parents:
            raise StoredFileMissingError(
                "DB 영상 경로가 storage 디렉터리 밖을 가리킴."
            )

    if not final_path.is_file():
        raise StoredFileMissingError(
            "DB에는 영상이 있지만 실제 "
            "clip.mp4 파일을 찾을 수 없음."
        )

    size_bytes = (
        final_path.stat().st_size
    )

    if size_bytes <= 0:
        raise StoredFileMissingError(
            "기존 clip.mp4가 비어 있음."
        )

    actual_sha256 = (
        calculate_file_sha256(
            final_path
        )
    )

    if not secrets.compare_digest(
        actual_sha256,
        normalized_expected_sha256,
    ):
        raise FileChecksumMismatchError(
            "기존 영상과 재전송 영상의 "
            "SHA-256이 일치하지 않음."
        )

    # 재시도 경로도 publish 직후 중단된 저장을 마무리한 뒤 ACK함.
    sync_stored_file(final_path)

    relative_path = (
        final_path
        .relative_to(PROJECT_ROOT)
        .as_posix()
    )

    return StoredFile(
        absolute_path=final_path,
        relative_path=relative_path,
        size_bytes=size_bytes,
        sha256=actual_sha256,
        reused_existing=True,
    )


async def save_mp4_file(
    upload_file: UploadFile,
    event_id: str,
    vehicle_id: str,
    event_time_ns: int,
    expected_sha256: str,
    repair_relative_path: str | None = None,
) -> StoredFile:
    """
    업로드 MP4를 임시 파일에 저장한 뒤
    최종 clip.mp4로 변경함.

    안정성 처리:

    1. 기존 파일이 있으면 SHA-256을 비교함.
    2. 업로드 중에는 .part 파일을 사용함.
    3. 업로드 완료 후 SHA-256을 검사함.
    4. SHA-256이 맞을 때만 clip.mp4로 변경함.
    """

    normalized_expected_sha256 = (
        normalize_sha256(
            expected_sha256
        )
    )

    final_path = build_video_path(
        event_id=event_id,
        vehicle_id=vehicle_id,
        event_time_ns=event_time_ns,
    )

    if repair_relative_path is not None:
        final_path = (PROJECT_ROOT / repair_relative_path).resolve()
        if (PROJECT_ROOT / "storage").resolve() not in final_path.parents:
            raise StoredFileMissingError("복원 경로가 storage 밖을 가리킴.")

    save_directory = final_path.parent

    save_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = (
        save_directory
        / f".{uuid4().hex}.part"
    )

    total_size = 0
    file_digest = hashlib.sha256()

    try:
        if final_path.exists() and repair_relative_path is None:
            return inspect_stored_mp4(
                event_id=event_id,
                vehicle_id=vehicle_id,
                expected_sha256=(
                    normalized_expected_sha256
                ),
                stored_relative_path=(
                    final_path
                    .relative_to(PROJECT_ROOT)
                    .as_posix()
                ),
            )

        with temporary_path.open(
            "xb"
        ) as output_file:
            while True:
                chunk = (
                    await upload_file.read(
                        CHUNK_SIZE
                    )
                )

                if not chunk:
                    break

                total_size += len(chunk)

                if (
                    total_size
                    > MAX_UPLOAD_SIZE
                ):
                    raise FileTooLargeError(
                        "영상 크기가 "
                        "100MB를 초과함."
                    )

                output_file.write(chunk)
                file_digest.update(chunk)

            output_file.flush()
            os.fsync(output_file.fileno())

        if total_size == 0:
            raise EmptyFileError(
                "업로드된 영상이 비어 있음."
            )

        actual_sha256 = (
            file_digest.hexdigest()
        )

        if not secrets.compare_digest(
            actual_sha256,
            normalized_expected_sha256,
        ):
            raise FileChecksumMismatchError(
                "전송된 영상의 SHA-256이 "
                "X-Evidence-Content-SHA256과 "
                "일치하지 않음."
            )

        # 동시에 같은 영상 요청이 들어와
        # 다른 요청이 먼저 최종 파일을 만든 경우임.
        if final_path.exists() and repair_relative_path is None:
            temporary_path.unlink(
                missing_ok=True
            )

            return inspect_stored_mp4(
                event_id=event_id,
                vehicle_id=vehicle_id,
                expected_sha256=(
                    normalized_expected_sha256
                ),
                stored_relative_path=(
                    final_path
                    .relative_to(PROJECT_ROOT)
                    .as_posix()
                ),
            )

        if repair_relative_path is not None:
            temporary_path.replace(final_path)
        else:
            # 여러 요청이 동시에 같은 경로를 게시해도 다른 원본을 덮어쓰지 않음.
            try:
                os.link(temporary_path, final_path)
            except FileExistsError:
                return inspect_stored_mp4(
                    event_id, vehicle_id, normalized_expected_sha256,
                    final_path.relative_to(PROJECT_ROOT).as_posix(),
                )
            finally:
                temporary_path.unlink(missing_ok=True)
        sync_directory_chain(final_path.parent)

        relative_path = (
            final_path
            .relative_to(PROJECT_ROOT)
            .as_posix()
        )

        return StoredFile(
            absolute_path=final_path,
            relative_path=relative_path,
            size_bytes=total_size,
            sha256=actual_sha256,
            reused_existing=False,
        )

    except Exception:
        temporary_path.unlink(
            missing_ok=True
        )

        raise

    finally:
        await upload_file.close()


def remove_stored_file(
    stored_file: StoredFile,
) -> None:
    """
    DB 기록 실패 시 이번 요청에서 생성한
    파일만 제거함.

    기존 파일을 재사용한 요청에서는
    정상 파일을 삭제하지 않음.
    """

    if stored_file.reused_existing:
        return

    stored_file.absolute_path.unlink(
        missing_ok=True
    )
