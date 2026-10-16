from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil

from app.db.admin_cleanup_repository import (
    delete_event_records,
    delete_user_records,
    get_event_id,
    get_user_deletion_target,
    list_event_file_paths,
    reset_demo_records,
)
from app.schemas.admin_actions import (
    AdminActionResponse,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]

STORAGE_ROOT = (
    BACKEND_ROOT
    / "storage"
)

ORIGINALS_ROOT = (
    STORAGE_ROOT
    / "originals"
)

MERGED_ROOT = (
    STORAGE_ROOT
    / "merged"
)

TRASH_ROOT = (
    STORAGE_ROOT
    / "trash"
)

EVENT_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{1,64}$"
)


@dataclass(frozen=True)
class StagedPath:
    original_path: Path
    trash_path: Path


def make_operation_id(
    operation_name: str,
) -> str:
    """
    휴지통 작업별 고유 폴더명을 생성함.
    """

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    return (
        f"{operation_name}_{timestamp}"
    )


def move_path_to_trash(
    source_path: Path,
    operation_directory: Path,
    category: str,
) -> StagedPath | None:
    """
    파일 또는 폴더가 존재하면
    storage/trash 아래로 이동함.
    """

    if not source_path.exists():
        return None

    destination_directory = (
        operation_directory
        / category
    )

    destination_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination_path = (
        destination_directory
        / source_path.name
    )

    shutil.move(
        str(source_path),
        str(destination_path),
    )

    return StagedPath(
        original_path=source_path,
        trash_path=destination_path,
    )


def restore_staged_paths(
    staged_paths: list[StagedPath],
) -> None:
    """
    DB 삭제가 실패하면 휴지통으로 옮긴
    파일을 원래 위치로 복구함.
    """

    for staged_path in reversed(
        staged_paths
    ):
        if not staged_path.trash_path.exists():
            continue

        staged_path.original_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.move(
            str(staged_path.trash_path),
            str(staged_path.original_path),
        )


def stage_event_files(
    event_id: str,
) -> tuple[list[StagedPath], Path]:
    """
    특정 사고의 원본·병합 영상 폴더를
    휴지통으로 이동함.
    """

    if not EVENT_ID_PATTERN.fullmatch(
        event_id
    ):
        raise ValueError(
            "event_id 형식이 올바르지 않음."
        )

    operation_directory = (
        TRASH_ROOT
        / make_operation_id(
            f"event_{event_id}"
        )
    )

    staged_paths: list[StagedPath] = []

    originals_root = ORIGINALS_ROOT.resolve()
    original_directories = {originals_root / event_id}
    for stored_path in list_event_file_paths(event_id):
        file_path = (BACKEND_ROOT / stored_path).resolve()
        relative_path = file_path.relative_to(originals_root)
        parts = relative_path.parts
        # 기존 event/vehicle/file과 날짜별 YYYY/MM/DD/event/vehicle/file 지원.
        if len(parts) == 3 and parts[0] == event_id:
            event_directory = originals_root / event_id
        elif (
            len(parts) == 6
            and parts[3] == event_id
            and re.fullmatch(r"\d{4}/\d{2}/\d{2}", "/".join(parts[:3]))
        ):
            event_directory = originals_root.joinpath(*parts[:4])
        else:
            raise ValueError("DB 영상 경로가 삭제할 사고의 원본 경로가 아님.")
        original_directories.add(event_directory)

    sources = [
        (
            path,
            (Path("originals") / path.parent.relative_to(originals_root)).as_posix(),
        )
        for path in sorted(original_directories)
    ]
    sources.append((MERGED_ROOT / event_id, "merged"))

    # 경로 전체를 먼저 검사하므로 잘못된 DB 경로로 다른 사고를 옮기지 않음.
    for source_path, category in sources:
        root = originals_root if category.startswith("originals") else MERGED_ROOT.resolve()
        resolved = source_path.resolve()
        if root not in resolved.parents or resolved.name != event_id:
            raise ValueError("영상 폴더가 삭제할 사고의 저장 경로를 벗어남.")

    try:
        for source_path, category in sources:
            result = move_path_to_trash(source_path, operation_directory, category)
            if result is not None:
                staged_paths.append(result)
    except Exception:
        restore_staged_paths(staged_paths)
        raise

    return (
        staged_paths,
        operation_directory,
    )


def stage_all_demo_files() -> tuple[
    list[StagedPath],
    Path,
]:
    """
    전체 원본·병합 영상 폴더의 내용을
    하나의 휴지통 작업 폴더로 이동함.
    """

    operation_directory = (
        TRASH_ROOT
        / make_operation_id(
            "reset_demo"
        )
    )

    staged_paths: list[StagedPath] = []

    sources = (
        (
            ORIGINALS_ROOT,
            "originals",
        ),
        (
            MERGED_ROOT,
            "merged",
        ),
    )

    for source_root, category in sources:
        if not source_root.exists():
            continue

        for child_path in list(
            source_root.iterdir()
        ):
            result = move_path_to_trash(
                child_path,
                operation_directory,
                category,
            )

            if result is not None:
                staged_paths.append(
                    result
                )

    return (
        staged_paths,
        operation_directory,
    )


def delete_event(
    event_id: str,
    confirmation: str,
) -> AdminActionResponse:
    """
    특정 사고의 DB 기록을 삭제하고
    영상 파일은 휴지통으로 이동함.
    """

    if confirmation != event_id:
        raise ValueError(
            "확인 문구가 event_id와 일치하지 않음."
        )

    existing_event_id = get_event_id(
        event_id
    )

    if existing_event_id is None:
        raise ValueError(
            "삭제할 사고를 찾을 수 없음."
        )

    staged_paths, trash_directory = (
        stage_event_files(
            event_id
        )
    )

    try:
        deleted_counts = (
            delete_event_records(
                event_id
            )
        )

    except Exception:
        restore_staged_paths(
            staged_paths
        )
        raise

    return AdminActionResponse(
        success=True,
        action="DELETE_EVENT",
        message=(
            f"{event_id} 사고 데이터를 삭제함."
        ),
        deleted_counts=deleted_counts,
        trash_location=str(
            trash_directory.relative_to(
                BACKEND_ROOT
            )
        ),
    )


def delete_user(
    current_admin_user_id: int,
    target_user_id: int,
    confirmation: str,
) -> AdminActionResponse:
    """
    일반 사용자 계정을 삭제함.

    현재 관리자와 다른 ADMIN 계정은
    웹에서 삭제할 수 없음.
    """

    if (
        current_admin_user_id
        == target_user_id
    ):
        raise ValueError(
            "현재 로그인한 관리자 자신은 "
            "삭제할 수 없음."
        )

    target = get_user_deletion_target(
        target_user_id
    )

    if target is None:
        raise ValueError(
            "삭제할 사용자를 찾을 수 없음."
        )

    if target.role == "ADMIN":
        raise ValueError(
            "관리자 계정은 웹에서 삭제할 수 없음."
        )

    if confirmation != target.username:
        raise ValueError(
            "확인 문구가 사용자 아이디와 "
            "일치하지 않음."
        )

    deleted_counts = delete_user_records(
        target_user_id
    )

    return AdminActionResponse(
        success=True,
        action="DELETE_USER",
        message=(
            f"{target.username} 사용자를 삭제함."
        ),
        deleted_counts=deleted_counts,
    )


def reset_demo_data(
    current_admin_user_id: int,
    confirmation: str,
) -> AdminActionResponse:
    """
    현재 관리자와 테이블 구조를 유지하면서
    모든 시연 데이터를 초기화함.
    """

    if confirmation != "RESET DEMO DATA":
        raise ValueError(
            "전체 초기화 확인 문구가 "
            "올바르지 않음."
        )

    staged_paths, trash_directory = (
        stage_all_demo_files()
    )

    try:
        deleted_counts = (
            reset_demo_records(
                current_admin_user_id
            )
        )

    except Exception:
        restore_staged_paths(
            staged_paths
        )
        raise

    return AdminActionResponse(
        success=True,
        action="RESET_DEMO",
        message=(
            "현재 관리자 계정을 제외한 "
            "시연 데이터를 초기화함."
        ),
        deleted_counts=deleted_counts,
        trash_location=str(
            trash_directory.relative_to(
                BACKEND_ROOT
            )
        ),
    )


def purge_trash(
    confirmation: str,
) -> AdminActionResponse:
    """
    storage/trash 내용을 완전히 삭제함.

    이 작업은 복구할 수 없음.
    """

    if confirmation != "PURGE SERVER TRASH":
        raise ValueError(
            "휴지통 삭제 확인 문구가 "
            "올바르지 않음."
        )

    removed_item_count = 0

    TRASH_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    for child_path in list(
        TRASH_ROOT.iterdir()
    ):
        if child_path.is_dir():
            shutil.rmtree(child_path)
        else:
            child_path.unlink()

        removed_item_count += 1

    return AdminActionResponse(
        success=True,
        action="PURGE_TRASH",
        message=(
            "서버 휴지통을 완전히 삭제함."
        ),
        deleted_counts={
            "trash_items": (
                removed_item_count
            )
        },
    )
