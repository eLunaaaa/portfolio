from pathlib import Path
from copy import deepcopy
import json
import math
import re
import shutil
import subprocess
from typing import Any, Literal
from app.config import get_setting

from app.db.merge_repository import (
    EvidenceRecord,
    list_latest_evidence,
)


NS_PER_SECOND = 1_000_000_000

# 화면에 표시할 시간대임.
#
# Unix Timestamp 자체는 UTC 기준이므로
# 한국 시간으로 표시하기 위해 9시간을 더함.
DISPLAY_TIMEZONE_OFFSET_SECONDS = 9 * 60 * 60

# 영상에 표시할 시간대 이름임.
DISPLAY_TIMEZONE_LABEL = "KST"

# 각 차량 화면에 표시할 글자 크기임.
TIMESTAMP_FONT_SIZE = 22
VEHICLE_LABEL_FONT_SIZE = 24

# video_merger.py의 위치는 backend/app/services임.
# parents[0] = services
# parents[1] = app
# parents[2] = backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent

MERGED_ROOT = BACKEND_DIR / "storage" / "merged"

EVENT_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{1,64}$"
)

MergeMode = Literal[
    "common",
    "extended",
]


class MergeError(RuntimeError):
    """
    영상 병합 과정에서 발생한 오류를 표현함.

    일반 RuntimeError와 구분해 두면 CLI에서
    병합 관련 오류를 사용자에게 읽기 좋은 형태로 표시할 수 있음.
    """


def excluded_merge_vehicle_ids() -> set[str]:
    """시연용 제외 차량은 원본 수신·보관은 유지하고 모든 병합에서 제외함."""
    return {value.strip() for value in get_setting(
        "EVIDENCE_MERGE_EXCLUDED_VEHICLES", "ACE_MERGE_EXCLUDED_VEHICLES", "CAR_03"
    ).split(",") if value.strip()}


def integer_median(values: list[int]) -> int:
    """
    정수 Timestamp 목록의 중앙값을 정수 상태로 계산함.

    Args:
        values:
            나노초 단위 Timestamp 목록임.

    Returns:
        목록의 중앙값을 int로 반환함.

    Raises:
        MergeError:
            입력 목록이 비어 있는 경우 발생함.

    필요한 이유:
        Unix 나노초 Timestamp는 약 10의 18승으로 매우 큼.
        먼저 float로 변환하면 일부 정밀도가 손실될 수 있으므로
        정수 상태에서 중앙값을 계산함.
    """

    if not values:
        raise MergeError(
            "중앙값을 계산할 Timestamp가 없음."
        )

    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2 == 1:
        return ordered[middle]

    return (
        ordered[middle - 1]
        + ordered[middle]
    ) // 2


def resolve_video_path(
    stored_path: str,
) -> Path:
    """
    DB에 저장된 영상 경로를 실제 파일 경로로 변환함.

    Args:
        stored_path:
            evidence.file_path에 저장된 문자열 경로임.

    Returns:
        실제 영상 파일을 가리키는 Path 객체임.

    필요한 이유:
        DB에는 다음처럼 상대경로가 저장될 수 있음.

        storage/originals/EVT_001/CAR_01/clip.mp4

        Python 프로그램을 어디에서 실행했는지에 따라
        상대경로의 기준 위치가 달라질 수 있으므로,
        backend·프로젝트 루트·현재 디렉터리를 순서대로 확인함.
    """

    path = Path(stored_path)

    if path.is_absolute():
        return path

    base_directories = (
        BACKEND_DIR,
        PROJECT_ROOT,
        Path.cwd(),
    )

    for base_directory in base_directories:
        candidate = (
            base_directory / path
        ).resolve()

        if candidate.exists():
            return candidate

    # 파일을 찾지 못했을 때는 가장 가능성 높은 경로를 반환함.
    # 이후 호출 함수가 경로를 포함한 오류를 발생시킴.
    return (BACKEND_DIR / path).resolve()


def probe_video_duration(
    video_path: Path,
) -> float:
    """
    ffprobe를 이용해 MP4의 실제 재생 시간을 측정함.

    Args:
        video_path:
            확인할 영상 파일 경로임.

    Returns:
        영상의 실제 재생 시간을 초 단위 float로 반환함.

    Raises:
        MergeError:
            ffprobe가 설치되지 않았거나,
            영상 파일을 읽을 수 없거나,
            영상 길이가 올바르지 않은 경우 발생함.

    필요한 이유:
        메타데이터에는 영상이 10초라고 기록되어 있어도
        녹화 실패나 파일 손상 때문에 실제 MP4는 8초일 수 있음.

        존재하지 않는 구간을 병합하려 하지 않도록
        메타데이터 길이와 실제 MP4 길이를 함께 확인함.
    """

    ffprobe_command = shutil.which("ffprobe")

    if ffprobe_command is None:
        raise MergeError(
            "ffprobe를 찾을 수 없음. "
            "brew install ffmpeg를 실행해야 함."
        )

    result = subprocess.run(
        [
            ffprobe_command,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise MergeError(
            f"영상 정보를 읽을 수 없음: {video_path}\n"
            f"{result.stderr.strip()}"
        )

    try:
        duration_seconds = float(
            result.stdout.strip()
        )
    except ValueError as error:
        raise MergeError(
            "ffprobe 결과를 숫자로 변환할 수 없음.\n"
            f"영상: {video_path}\n"
            f"출력: {result.stdout!r}"
        ) from error

    if duration_seconds <= 0:
        raise MergeError(
            f"영상 길이가 0 이하임: {video_path}"
        )

    return duration_seconds


def prepare_inputs(
    event_id: str,
) -> list[dict[str, Any]]:
    """
    DB에서 영상 기록을 가져오고 실제 파일 상태를 검사함.

    Args:
        event_id:
            처리할 사고 ID임.

    Returns:
        병합에 사용할 차량별 영상 정보 목록을 반환함.

        각 항목에는 다음 내용이 포함됨.

        record:
            DB에서 조회한 EvidenceRecord임.

        video_path:
            실제 영상 파일의 절대경로임.

        actual_duration_seconds:
            ffprobe로 확인한 MP4 길이임.

        effective_clip_end_ns:
            실제로 사용할 수 있는 영상 종료 시각임.

    Raises:
        MergeError:
            차량 영상이 1개 미만이거나,
            영상 파일이 없거나,
            영상 시간이 잘못된 경우 발생함.

    필요한 이유:
        build_merge_plan() 안에서 DB 조회, 파일 확인,
        시간 계산을 모두 수행하면 함수가 너무 복잡해짐.

        입력 준비 작업을 별도 함수로 분리하여
        문제가 DB인지 파일인지 쉽게 구분할 수 있게 함.
    """

    records = list_latest_evidence(event_id)

    if len(records) < 1:
        raise MergeError(
            "병합하려면 저장된 차량 영상이 "
            f"최소 1개 필요함. 현재 {len(records)}개임."
        )

    prepared_inputs: list[dict[str, Any]] = []

    for record in records:
        if record.vehicle_id in excluded_merge_vehicle_ids():
            continue
        video_path = resolve_video_path(
            record.video_path
        )

        if not video_path.exists():
            raise MergeError(
                "DB에는 기록되어 있지만 영상 파일이 없음.\n"
                f"vehicle_id: {record.vehicle_id}\n"
                f"DB 경로: {record.video_path}\n"
                f"확인한 경로: {video_path}"
            )

        actual_duration_seconds = (
            probe_video_duration(video_path)
        )

        actual_duration_ns = round(
            actual_duration_seconds
            * NS_PER_SECOND
        )

        actual_file_end_ns = (
            record.clip_start_time_ns
            + actual_duration_ns
        )

        # 메타데이터 종료 시각과 실제 MP4 종료 시각 중
        # 더 빠른 시각을 유효 종료 시각으로 사용함.
        effective_clip_end_ns = min(
            record.clip_end_time_ns,
            actual_file_end_ns,
        )

        if (
            effective_clip_end_ns
            <= record.clip_start_time_ns
        ):
            raise MergeError(
                "영상 종료 시각이 시작 시각보다 "
                "빠르거나 같음.\n"
                f"vehicle_id: {record.vehicle_id}\n"
                f"start: {record.clip_start_time_ns}\n"
                f"end: {effective_clip_end_ns}"
            )

        prepared_inputs.append(
            {
                "record": record,
                "video_path": video_path,
                "actual_duration_seconds": (
                    actual_duration_seconds
                ),
                "effective_clip_end_ns": (
                    effective_clip_end_ns
                ),
            }
        )

    if not prepared_inputs:
        raise MergeError("병합 제외 차량 외에 사용할 영상이 없음. 원본은 보관됨.")
    return prepared_inputs


def calculate_timeline(
    prepared_inputs: list[dict[str, Any]],
    event_time_ns: int,
    before_seconds: float,
    after_seconds: float,
    mode: MergeMode,
) -> tuple[int, int]:
    """
    병합 결과의 전체 시작·종료 시각을 계산함.

    Args:
        prepared_inputs:
            prepare_inputs()가 준비한 차량별 영상 목록임.

        event_time_ns:
            공통 사고 발생 시각임.

        before_seconds:
            사고 전 최대 몇 초까지 포함할지 지정함.

        after_seconds:
            사고 후 최대 몇 초까지 포함할지 지정함.

        mode:
            common 또는 extended임.

    Returns:
        다음 튜플을 반환함.

        (timeline_start_ns, timeline_end_ns)

    mode별 차이:

        common:
            모든 차량 영상이 동시에 존재하는 공통 구간임.

        extended:
            한 차량 영상이라도 존재하는 전체 구간임.
            영상이 없는 차량의 구간은 이후 검은 화면으로 채움.
    """

    requested_start_ns = (
        event_time_ns
        - round(before_seconds * NS_PER_SECOND)
    )

    requested_end_ns = (
        event_time_ns
        + round(after_seconds * NS_PER_SECOND)
    )

    clip_start_times = [
        item["record"].clip_start_time_ns
        for item in prepared_inputs
    ]

    clip_end_times = [
        item["effective_clip_end_ns"]
        for item in prepared_inputs
    ]

    if mode == "common":
        # 모든 차량이 촬영한 공통 구간임.
        #
        # 가장 늦게 시작한 영상부터
        # 가장 일찍 끝난 영상까지만 사용함.
        timeline_start_ns = max(
            requested_start_ns,
            max(clip_start_times),
        )

        timeline_end_ns = min(
            requested_end_ns,
            min(clip_end_times),
        )

    elif mode == "extended":
        # 한 차량이라도 촬영한 전체 구간임.
        #
        # 가장 먼저 시작한 영상부터
        # 가장 늦게 끝난 영상까지 사용함.
        #
        # 단, before_seconds와 after_seconds를
        # 최대 허용 범위로 사용하여 비정상적으로 긴
        # 결과가 만들어지는 것을 방지함.
        timeline_start_ns = max(
            requested_start_ns,
            min(clip_start_times),
        )

        timeline_end_ns = min(
            requested_end_ns,
            max(clip_end_times),
        )

    else:
        raise MergeError(
            f"지원하지 않는 병합 모드임: {mode}"
        )

    if timeline_end_ns <= timeline_start_ns:
        raise MergeError(
            "병합할 수 있는 시간 구간이 없음.\n"
            f"mode: {mode}\n"
            "event_time_ns, clip_start_time_ns, "
            "clip_end_time_ns를 확인해야 함."
        )

    if not (
        timeline_start_ns
        <= event_time_ns
        <= timeline_end_ns
    ):
        raise MergeError(
            "결과 타임라인 안에 사고 시각이 포함되지 않음.\n"
            "사고 Timestamp 또는 영상 구간을 확인해야 함."
        )

    return (
        timeline_start_ns,
        timeline_end_ns,
    )


def build_merge_plan(
    event_id: str,
    before_seconds: float = 10.0,
    after_seconds: float = 20.0,
    mode: MergeMode = "extended",
) -> dict[str, Any]:
    """
    DB와 영상 파일을 분석하여 병합 계획을 생성함.

    이 함수는 FFmpeg를 실행하지 않음.
    어떤 영상을 몇 초부터 자르고,
    앞뒤에 검은 화면을 얼마나 추가할지만 계산함.

    Args:
        event_id:
            병합할 사고 ID임.

        before_seconds:
            사고 전 최대 포함 시간임.

        after_seconds:
            사고 후 최대 포함 시간임.

        mode:
            common 또는 extended임.

    Returns:
        FFmpeg 실행에 필요한 병합 계획을 dict로 반환함.

    디버깅 방법:
        CLI에서 --plan-only를 사용하면 이 함수 결과만 확인할 수 있음.

        python -m app.cli.merge_event \
          EVT_001 \
          --mode extended \
          --plan-only
    """

    if not EVENT_ID_PATTERN.fullmatch(event_id):
        raise MergeError(
            "event_id는 영문자, 숫자, 밑줄, "
            "하이픈만 사용할 수 있음."
        )

    if mode not in (
        "common",
        "extended",
    ):
        raise MergeError(
            "mode는 common 또는 extended여야 함."
        )

    if before_seconds < 0:
        raise MergeError(
            "before_seconds는 0 이상이어야 함."
        )

    if after_seconds < 0:
        raise MergeError(
            "after_seconds는 0 이상이어야 함."
        )

    if before_seconds == 0 and after_seconds == 0:
        raise MergeError(
            "병합할 시간 범위가 0초임."
        )

    prepared_inputs = prepare_inputs(event_id)

    # 사고 차량은 최종 격자의 첫 번째 칸에 배치함.
    # 사고 차량 영상이 없다면 아래에서 검은 타일을 추가함.
    accident_vehicle_id = str(
        prepared_inputs[0][
            "record"
        ].accident_vehicle_id
    )

    prepared_inputs.sort(
        key=lambda item: (
            0
            if item["record"].vehicle_id
            == accident_vehicle_id
            else 1,
            item["record"].vehicle_id,
        )
    )

    event_times = [
        item["record"].event_time_ns
        for item in prepared_inputs
    ]

    # 현재 DB 구조에서는 events 테이블의 사고 시각이
    # JOIN되어 모든 차량에 동일하게 들어옴.
    # 중앙값 계산은 잘못된 중복 데이터에 대비한 방어 로직임.
    canonical_event_time_ns = integer_median(
        event_times
    )

    event_time_spread_ns = (
        max(event_times)
        - min(event_times)
    )

    (
        timeline_start_ns,
        timeline_end_ns,
    ) = calculate_timeline(
        prepared_inputs=prepared_inputs,
        event_time_ns=canonical_event_time_ns,
        before_seconds=before_seconds,
        after_seconds=after_seconds,
        mode=mode,
    )

    timeline_duration_ns = (
        timeline_end_ns
        - timeline_start_ns
    )

    timeline_duration_seconds = (
        timeline_duration_ns
        / NS_PER_SECOND
    )

    accident_position_seconds = (
        canonical_event_time_ns
        - timeline_start_ns
    ) / NS_PER_SECOND

    warnings: list[str] = []

    if event_time_spread_ns != 0:
        warnings.append(
            "차량별 event_time_ns가 서로 다름. "
            "사고 차량이 생성한 Timestamp를 "
            "그대로 전달했는지 확인해야 함."
        )

    non_synced_vehicles = [
        item["record"].vehicle_id
        for item in prepared_inputs
        if item["record"].time_sync_status
        != "SYNCED"
    ]

    if non_synced_vehicles:
        warnings.append(
            "SYNCED 상태가 아닌 차량이 포함됨: "
            + ", ".join(non_synced_vehicles)
        )

    plan_inputs: list[dict[str, Any]] = []

    for item in prepared_inputs:
        record: EvidenceRecord = item["record"]

        # 현재 차량 영상에서 실제로 사용할 수 있는 시작 시각임.
        #
        # 영상이 타임라인보다 먼저 시작했다면
        # 타임라인 시작 전 구간은 잘라냄.
        visible_start_ns = max(
            timeline_start_ns,
            record.clip_start_time_ns,
        )

        # 현재 차량 영상에서 실제로 사용할 수 있는 종료 시각임.
        #
        # 영상이 타임라인보다 늦게 끝난다면
        # 타임라인 종료 이후 구간은 잘라냄.
        visible_end_ns = min(
            timeline_end_ns,
            item["effective_clip_end_ns"],
        )

        if visible_end_ns <= visible_start_ns:
            warnings.append(
                f"{record.vehicle_id} 영상은 "
                "선택한 시간 범위와 겹치지 않아 제외됨."
            )

            continue

        # MP4 안에서 몇 초 지점부터 읽을지 계산함.
        trim_start_seconds = (
            visible_start_ns
            - record.clip_start_time_ns
        ) / NS_PER_SECOND

        # MP4에서 실제로 보여줄 길이임.
        visible_duration_seconds = (
            visible_end_ns
            - visible_start_ns
        ) / NS_PER_SECOND

        # 전체 타임라인 시작 후,
        # 이 차량 영상이 시작되기 전까지의 검은 화면 길이임.
        pad_before_seconds = (
            visible_start_ns
            - timeline_start_ns
        ) / NS_PER_SECOND

        # 이 차량 영상이 끝난 후,
        # 전체 타임라인이 끝날 때까지의 검은 화면 길이임.
        pad_after_seconds = (
            timeline_end_ns
            - visible_end_ns
        ) / NS_PER_SECOND

        plan_inputs.append(
            {
                "evidence_id": record.id,
                "vehicle_id": record.vehicle_id,
                "is_accident_vehicle": (
                    record.vehicle_id
                    == accident_vehicle_id
                ),
                "is_placeholder": False,
                "video_path": str(item["video_path"]),
                "event_time_ns": record.event_time_ns,
                "clip_start_time_ns": (
                    record.clip_start_time_ns
                ),
                "metadata_clip_end_ns": (
                    record.clip_end_time_ns
                ),
                "effective_clip_end_ns": (
                    item["effective_clip_end_ns"]
                ),
                "actual_duration_seconds": (
                    item["actual_duration_seconds"]
                ),
                "visible_start_ns": visible_start_ns,
                "visible_end_ns": visible_end_ns,
                "trim_start_seconds": (
                    trim_start_seconds
                ),
                "visible_duration_seconds": (
                    visible_duration_seconds
                ),
                "pad_before_seconds": (
                    pad_before_seconds
                ),
                "pad_after_seconds": (
                    pad_after_seconds
                ),
                "time_sync_status": (
                    record.time_sync_status
                ),
                "chrony_offset_ms": (
                    record.chrony_offset_ms
                ),
            }
        )

    if len(plan_inputs) < 1:
        raise MergeError(
            "선택한 시간 범위와 겹치는 차량 영상이 "
            f"1개 미만임. 현재 {len(plan_inputs)}개임."
        )

    accident_video_received = any(
        input_item["is_accident_vehicle"]
        for input_item in plan_inputs
    )

    if not accident_video_received and accident_vehicle_id not in excluded_merge_vehicle_ids():
        # 사고 차량 영상이 도착하지 않은 경우에도
        # 첫 번째 칸의 의미가 바뀌지 않도록 검은 타일을 만듦.
        plan_inputs.insert(
            0,
            {
                "evidence_id": None,
                "vehicle_id": accident_vehicle_id,
                "is_accident_vehicle": True,
                "is_placeholder": True,
                "video_path": None,
                "event_time_ns": canonical_event_time_ns,
                "visible_start_ns": timeline_start_ns,
                "visible_end_ns": timeline_end_ns,
                "visible_duration_seconds": (
                    timeline_duration_seconds
                ),
                "pad_before_seconds": 0.0,
                "pad_after_seconds": 0.0,
                "time_sync_status": "MISSING",
                "chrony_offset_ms": None,
            },
        )

        warnings.append(
            "사고 차량 영상이 도착하지 않아 "
            "첫 번째 칸을 검은 화면으로 표시함: "
            f"{accident_vehicle_id}"
        )

    return {
        "event_id": event_id,
        "accident_vehicle_id": accident_vehicle_id,
        "accident_video_received": (
            accident_video_received
        ),
        "mode": mode,
        "vehicle_count": len(plan_inputs),
        "canonical_event_time_ns": (
            canonical_event_time_ns
        ),
        "event_time_spread_ns": (
            event_time_spread_ns
        ),
        "before_seconds_limit": before_seconds,
        "after_seconds_limit": after_seconds,
        "timeline_start_ns": timeline_start_ns,
        "timeline_end_ns": timeline_end_ns,
        "timeline_duration_seconds": (
            timeline_duration_seconds
        ),
        "accident_position_seconds": (
            accident_position_seconds
        ),
        "warnings": warnings,
        "inputs": plan_inputs,
        "excluded_vehicle_ids": sorted(excluded_merge_vehicle_ids()),
    }

def find_timestamp_font() -> Path | None:
    """
    운영체제에서 Timestamp 표시에 사용할 폰트를 찾음.

    Returns:
        사용할 수 있는 폰트가 있으면 해당 Path를 반환함.
        찾지 못하면 None을 반환함.

    필요한 이유:
        FFmpeg drawtext는 글자를 그리기 위해 폰트가 필요함.

        현재 개발 환경은 Mac이지만,
        최종 서버가 Windows일 수 있으므로
        macOS·Linux·Windows의 대표적인 폰트 경로를
        순서대로 확인함.
    """

    font_candidates = (
        # macOS
        Path(
            "/System/Library/Fonts/"
            "AppleSDGothicNeo.ttc"
        ),
        Path(
            "/System/Library/Fonts/"
            "AppleGothic.ttf"
        ),
        Path(
            "/System/Library/Fonts/"
            "Supplemental/Arial.ttf"
        ),
        Path(
            "/System/Library/Fonts/"
            "Helvetica.ttc"
        ),

        # Raspberry Pi·Ubuntu
        Path(
            "/usr/share/fonts/truetype/"
            "dejavu/DejaVuSans.ttf"
        ),

        # Windows
        Path(
            "C:/Windows/Fonts/arial.ttf"
        ),
    )

    for font_path in font_candidates:
        if font_path.exists():
            return font_path

    return None


def ffmpeg_supports_filter(
    ffmpeg_command: str,
    filter_name: str,
) -> bool:
    """
    현재 FFmpeg가 특정 필터를 지원하는지 확인함.

    Args:
        ffmpeg_command:
            실행할 FFmpeg 프로그램 경로임.

        filter_name:
            확인할 필터 이름임.
            이번에는 drawtext를 전달함.

    Returns:
        필터가 있으면 True,
        없으면 False를 반환함.

    필요한 이유:
        FFmpeg가 설치되어 있어도 빌드 설정에 따라
        drawtext 필터가 빠져 있을 수 있음.

        병합을 시작하기 전에 이를 확인하면
        긴 FFmpeg 오류 대신 명확한 메시지를 제공할 수 있음.
    """

    result = subprocess.run(
        [
            ffmpeg_command,
            "-hide_banner",
            "-filters",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    filter_output = (
        result.stdout
        + "\n"
        + result.stderr
    )

    return re.search(
        rf"\b{re.escape(filter_name)}\b",
        filter_output,
    ) is not None


def escape_ffmpeg_filter_value(
    value: str,
) -> str:
    """
    FFmpeg 필터 옵션 안에 경로를 안전하게 넣도록 변환함.

    Args:
        value:
            폰트나 텍스트 파일의 경로임.

    Returns:
        FFmpeg 필터 문자열에서 사용할 수 있도록
        특수문자를 이스케이프한 문자열임.

    필요한 이유:
        Windows 경로의 콜론이나,
        경로 안의 따옴표·역슬래시는
        FFmpeg 필터 문법과 충돌할 수 있음.
    """

    return (
        value
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def create_timestamp_template(
    output_directory: Path,
    vehicle_id: str,
    visible_start_ns: int,
) -> Path:
    """
    차량 영상에 표시할 절대시각 Timestamp 템플릿 파일을 생성함.

    visible_start_ns:
        병합 영상에서 실제 차량 영상의 첫 프레임이 나타내는
        Unix Epoch 기준 나노초 시각임.

    생성되는 표시 형식:
        2026-07-29 15h47m22.266s KST
    """

    # Unix Timestamp는 UTC 기준이므로
    # 화면에 한국 시각을 표시하기 위해 9시간을 더함.
    kst_offset_ns = 9 * 60 * 60 * 1_000_000_000

    display_start_ns = visible_start_ns + kst_offset_ns

    # pts:gmtime에 넘길 초 단위 시작 시각임.
    #
    # 소수점 이하까지 유지해야 영상 시작 시각의
    # 밀리초 위치가 정확히 반영됨.
    display_start_seconds = (
        display_start_ns / 1_000_000_000
    )

    # 영상 시작 시각의 밀리초 부분만 분리함.
    #
    # 예:
    # 1785307643266223000 ns
    # → 밀리초 부분은 266
    start_millisecond = (
        visible_start_ns // 1_000_000
    ) % 1000

    # 날짜·시·분·초는 pts:gmtime으로 계산함.
    #
    # 밀리초는 다음 수식으로 별도 계산함.
    #
    # 시작 밀리초 + 현재 프레임의 PTS × 1000
    #
    # mod(..., 1000)을 사용하여 000~999 범위를 유지함.
    # :d:3은 정수를 항상 세 자리로 출력함.
    timestamp_text = (
        f"%{{pts:gmtime:{display_start_seconds:.9f}:"
        f"%Y-%m-%d %Hh%Mm%S}}"
        f".%{{eif:mod("
        f"{start_millisecond}+floor(t*1000),"
        f"1000):d:3}}s KST"
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    template_path = (
        output_directory
        / f"timestamp_{vehicle_id}.txt"
    )

    template_path.write_text(
        timestamp_text,
        encoding="utf-8",
    )

    return template_path

def probe_video_aspect(video_path: Path) -> float:
    """원본의 표시 비율을 읽어 고정 16:9 타일의 불필요한 여백을 방지함."""
    result = subprocess.run([
        shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,sample_aspect_ratio", "-of", "json", str(video_path),
    ], capture_output=True, text=True, check=False)
    try:
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        sar = stream.get("sample_aspect_ratio", "1:1")
        numerator, denominator = map(int, sar.split(":")) if sar != "N/A" else (1, 1)
        aspect = width / height * (numerator / denominator if denominator and numerator else 1)
        if result.returncode or not math.isfinite(aspect) or aspect <= 0:
            raise ValueError("invalid aspect")
        return aspect
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise MergeError(f"원본 영상 비율을 확인할 수 없음: {video_path}") from error


def build_tile_layout(
    count: int, width: int, height: int,
    view: Literal["all", "witness", "own"] = "all",
) -> list[dict[str, int]]:
    """빈 칸 없이 배치하며 입력 순서(존재하면 사고 차량 우선)를 유지함.

    전체 보기 3개는 사고 차량을 왼쪽에 크게, 목격 차량 3개는 같은 크기로 가로 배치함.
    5개는 위 2개·아래 3개로 배치함.
    6개 이상은 행별 개수를 균등하게 나누고 마지막 행을 넓혀 빈 칸을 없앰.
    """
    if count < 1:
        raise MergeError("레이아웃에 영상이 최소 1개 필요함.")
    if count == 3 and view == "witness":
        return [dict(x=index * width, y=0, width=width, height=height)
                for index in range(count)]
    if count == 3:
        return [dict(x=0, y=0, width=width * 2, height=height * 2),
                dict(x=width * 2, y=0, width=width, height=height),
                dict(x=width * 2, y=height, width=width, height=height)]
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    quotient, remainder = divmod(count, rows)
    row_counts = [quotient + (index >= rows - remainder) for index in range(rows)]
    canvas_width = columns * width
    tiles = []
    y = 0
    for row_count in row_counts:
        # 행 끝을 정확히 맞추고 H.264용 짝수 좌표를 유지함.
        boundaries = [round(index * canvas_width / row_count / 2) * 2 for index in range(row_count + 1)]
        row_height = max(2, round(canvas_width / row_count * height / width / 2) * 2)
        for index in range(row_count):
            tiles.append(dict(x=boundaries[index], y=y,
                              width=boundaries[index + 1] - boundaries[index], height=row_height))
        y += row_height
    return tiles


def merge_event(
    event_id: str,
    before_seconds: float = 10.0,
    after_seconds: float = 20.0,
    mode: MergeMode = "extended",
    tile_width: int = 640,
    tile_height: int | None = None,
    fps: int = 30,
    overwrite: bool = False,
    revision: int | None = None,
    view: Literal["all", "witness", "own"] = "all",
    _source_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    병합 계획을 계산하고,
    차량별 절대 Timestamp를 표시한 최종 MP4를 생성함.

    Args:
        event_id:
            병합할 사고 ID임.

        before_seconds:
            사고 전 최대 포함 시간임.

        after_seconds:
            사고 후 최대 포함 시간임.

        mode:
            common 또는 extended임.

        tile_width:
            차량 영상 한 칸의 너비임.

        tile_height:
            기본 타일 높이임. None이면 원본 표시 비율로 자동 계산함.

        fps:
            최종 영상의 프레임률임.

        overwrite:
            기존 결과 파일을 덮어쓸지 지정함.

        revision:
            자동 재병합 결과의 번호임.
            값이 있으면 merged_extended_r2.mp4처럼
            기존 결과를 보존하는 파일명을 사용함.

    Returns:
        병합 계획과 결과 파일 정보를 dict로 반환함.

    주요 처리 순서:
        1. 병합 계획을 계산함.
        2. 차량별 원본 구간을 trim함.
        3. 각 원본 프레임에 절대시각을 표시함.
        4. 영상이 없는 앞뒤 구간을 검은 화면으로 채움.
        5. 차량 ID는 검은 화면에서도 보이게 표시함.
        6. 모든 차량 영상을 격자로 병합함.
    """

    if tile_width <= 0 or (tile_height is not None and tile_height <= 0):
        raise MergeError(
            "타일 크기는 0보다 커야 함."
        )

    if (
        tile_width % 2 != 0
        or (tile_height is not None and tile_height % 2 != 0)
    ):
        raise MergeError(
            "H.264 호환성을 위해 타일 너비와 높이는 "
            "짝수여야 함."
        )

    if fps <= 0:
        raise MergeError(
            "fps는 0보다 커야 함."
        )

    ffmpeg_command = shutil.which("ffmpeg")

    if ffmpeg_command is None:
        raise MergeError(
            "ffmpeg를 찾을 수 없음. "
            "brew install ffmpeg를 실행해야 함."
        )

    if not ffmpeg_supports_filter(
        ffmpeg_command,
        "drawtext",
    ):
        raise MergeError(
            "현재 FFmpeg에서 drawtext 필터를 "
            "지원하지 않음.\n"
            "ffmpeg -filters | grep drawtext로 확인해야 함."
        )

    if view not in {"all", "witness", "own"}:
        raise MergeError("지원하지 않는 영상 보기 모드임.")
    plan = deepcopy(_source_plan) if _source_plan is not None else build_merge_plan(
        event_id=event_id,
        before_seconds=before_seconds,
        after_seconds=after_seconds,
        mode=mode,
    )

    # 보기별로 시간을 다시 계산하지 않고 전체 병합의 동일 타임라인을 사용함.
    plan["inputs"] = [item for item in plan["inputs"]
                      if item["vehicle_id"] not in excluded_merge_vehicle_ids()
                      and (view == "all" or (not item.get("is_placeholder") and
                           bool(item.get("is_accident_vehicle")) == (view == "own")))]
    if not plan["inputs"]:
        raise MergeError("이 영상 모드에 표시할 차량 영상이 없음.")
    plan["view"] = view
    plan["vehicle_count"] = len(plan["inputs"])

    if tile_height is None:
        aspects = sorted(probe_video_aspect(Path(item["video_path"]))
                         for item in plan["inputs"] if not item.get("is_placeholder"))
        aspect = aspects[len(aspects) // 2]
        tile_height = max(2, round(tile_width / aspect / 2) * 2)
    tiles = build_tile_layout(len(plan["inputs"]), tile_width, tile_height, view=view)

    output_directory = (
        MERGED_ROOT / event_id
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    if revision is not None and revision <= 0:
        raise MergeError(
            "revision은 1 이상이어야 함."
        )

    output_stem = (
        f"merged_{mode}"
        if revision is None
        else f"merged_{mode}_r{revision}"
    )
    if view != "all":
        output_stem += f"_{view}"

    output_path = (
        output_directory
        / f"{output_stem}.mp4"
    )

    temporary_output_path = (
        output_directory
        / f"{output_stem}.building.mp4"
    )

    plan_path = output_directory / (output_stem.replace("merged_", "merge_plan_", 1) + ".json")

    log_path = output_directory / (output_stem.replace("merged_", "ffmpeg_", 1) + ".log")

    if output_path.exists() and not overwrite:
        raise MergeError(
            f"결과 영상이 이미 존재함: {output_path}\n"
            "다시 만들려면 --overwrite를 사용해야 함."
        )

    temporary_output_path.unlink(
        missing_ok=True
    )

    timestamp_font_path = (
        find_timestamp_font()
    )

    if timestamp_font_path is None:
        font_option = ""

    else:
        escaped_font_path = (
            escape_ffmpeg_filter_value(
                str(timestamp_font_path)
            )
        )

        font_option = (
            f":fontfile='{escaped_font_path}'"
        )

    input_count = len(plan["inputs"])

    columns = 3 if view == "witness" and input_count == 3 else math.ceil(math.sqrt(input_count))
    rows = math.ceil(input_count / columns)

    command: list[str] = [
        ffmpeg_command,
        "-hide_banner",
        "-y",
    ]

    next_ffmpeg_input_index = 0

    for input_item in plan["inputs"]:
        if input_item.get("is_placeholder"):
            continue

        input_item["ffmpeg_input_index"] = (
            next_ffmpeg_input_index
        )

        command.extend(
            [
                "-i",
                input_item["video_path"],
            ]
        )

        next_ffmpeg_input_index += 1

    filter_parts: list[str] = []

    timeline_duration_seconds = float(
        plan["timeline_duration_seconds"]
    )

    timestamp_template_paths: list[str] = []

    for index, input_item in enumerate(
        plan["inputs"]
    ):
        vehicle_id = str(
            input_item["vehicle_id"]
        )

        is_accident_vehicle = bool(
            input_item.get(
                "is_accident_vehicle",
                False,
            )
        )

        is_placeholder = bool(
            input_item.get(
                "is_placeholder",
                False,
            )
        )

        width = tiles[index]["width"]
        height = tiles[index]["height"]
        label_font_size = max(12, round(VEHICLE_LABEL_FONT_SIZE * width / tile_width))
        timestamp_font_size = max(12, round(TIMESTAMP_FONT_SIZE * width / tile_width))

        vehicle_label = (
            f"사고차량 · {vehicle_id}"
            if is_accident_vehicle
            else vehicle_id
        )

        # 타일은 여백 없이 붙이고 검은 구분선을 사용함. 사고 차량만 노란색임.
        accident_border_filter = (
            f",drawbox=x=0:y=0:w=iw:h=ih:color={'yellow' if is_accident_vehicle else 'black'}:t=4"
        )

        escaped_vehicle_label = (
            escape_ffmpeg_filter_value(
                vehicle_label
            )
        )

        # 사고 차량 영상이 오지 않은 경우에는
        # 입력 MP4 대신 전체 길이의 검은 화면을 생성함.
        if is_placeholder:
            placeholder_label_filter = (
                "drawtext="
                f"text='{escaped_vehicle_label}'"
                f"{font_option}:"
                "fontcolor=yellow:"
                f"fontsize={label_font_size}:"
                "box=1:"
                "boxcolor=black@0.70:"
                "boxborderw=6:"
                "x=12:"
                "y=12"
            )

            filter_parts.append(
                "color="
                f"c=black:s={width}x{height}:"
                f"r={fps}:d={timeline_duration_seconds:.9f},"
                "format=yuv420p,"
                "setsar=1,"
                f"{placeholder_label_filter}{accident_border_filter}"
                f"[v{index}]"
            )

            continue

        trim_start_seconds = float(
            input_item["trim_start_seconds"]
        )

        visible_duration_seconds = float(
            input_item["visible_duration_seconds"]
        )

        pad_before_seconds = float(
            input_item["pad_before_seconds"]
        )

        pad_after_seconds = float(
            input_item["pad_after_seconds"]
        )

        visible_start_ns = int(
            input_item["visible_start_ns"]
        )

        timestamp_template_path = (
            create_timestamp_template(
                output_directory=output_directory,
                vehicle_id=vehicle_id,
                visible_start_ns=visible_start_ns,
            )
        )

        timestamp_template_paths.append(
            str(timestamp_template_path)
        )

        escaped_template_path = (
            escape_ffmpeg_filter_value(
                str(timestamp_template_path)
            )
        )

        timestamp_drawtext_filter = (
            f"drawtext="
            f"textfile='{escaped_template_path}'"
            f"{font_option}:"
            "reload=0:"
            "fontcolor=white:"
            f"fontsize={timestamp_font_size}:"
            "box=1:"
            "boxcolor=black@0.70:"
            "boxborderw=6:"
            "x=12:"
            "y=h-text_h-12"
        )

        # 차량 ID는 패딩 이후에 표시함.
        # 따라서 실제 영상이 없는 검은 구간에서도
        # 어느 차량 칸인지 확인할 수 있음.
        vehicle_label_filter = (
            f"drawtext="
            f"text='{escaped_vehicle_label}'"
            f"{font_option}:"
            "fontcolor=yellow:"
            f"fontsize={label_font_size}:"
            "box=1:"
            "boxcolor=black@0.70:"
            "boxborderw=6:"
            "x=12:"
            "y=12"
        )

        filter_parts.append(
            f"[{input_item['ffmpeg_input_index']}:v]"

            # 원본 MP4에서 현재 타임라인에 필요한 구간만 자름.
            f"trim=start={trim_start_seconds:.9f}:"
            f"duration={visible_duration_seconds:.9f},"

            # 잘린 원본 영상의 PTS를 0부터 다시 시작함.
            "setpts=PTS-STARTPTS,"

            # 차량 영상들의 fps를 통일함.
            f"fps={fps},"

            # 영상 비율을 유지하면서 타일 안에 맞춤.
            f"scale={width}:{height}:"
            "force_original_aspect_ratio=decrease,"

            # 남는 화면을 검은색으로 채움.
            f"pad={width}:{height}:"
            "(ow-iw)/2:(oh-ih)/2:color=black,"

            "format=yuv420p,"
            "setsar=1,"

            # 중요:
            # 원본 프레임에 차량별 절대 Timestamp를 먼저 표시함.
            #
            # 이 시점의 PTS는 현재 차량 영상의 시작부터 흐르므로
            # visible_start_ns + PTS가 실제 프레임 시각이 됨.
            f"{timestamp_drawtext_filter},"

            # 해당 차량 영상이 없는 앞뒤 구간을
            # 검은 화면으로 채움.
            "tpad="
            "start_mode=add:"
            f"start_duration={pad_before_seconds:.9f}:"
            "stop_mode=add:"
            f"stop_duration={pad_after_seconds:.9f}:"
            "color=black,"

            # 전체 타임라인 길이에 정확히 맞춤.
            f"trim=start=0:"
            f"duration={timeline_duration_seconds:.9f},"

            # 패딩 이후 PTS를 다시 정리함.
            "setpts=PTS-STARTPTS,"

            # 검은 화면에서도 차량 ID가 보이도록
            # 차량 라벨은 tpad 이후 적용함.
            f"{vehicle_label_filter}{accident_border_filter}"

            f"[v{index}]"
        )

    layout_positions: list[str] = []

    for index in range(input_count):
        x_position = tiles[index]["x"]
        y_position = tiles[index]["y"]

        layout_positions.append(
            f"{x_position}_{y_position}"
        )

    layout = "|".join(
        layout_positions
    )

    input_labels = "".join(
        f"[v{index}]"
        for index in range(input_count)
    )

    if input_count == 1:
        # FFmpeg xstack 필터는 입력이 2개 이상일 때 사용함.
        # 영상이 한 개뿐이면 이미 타일 크기로 정규화된 v0를
        # 그대로 최종 출력 라벨에 연결함.
        filter_parts.append(
            "[v0]null[output_video]"
        )

    else:
        filter_parts.append(
            f"{input_labels}"
            f"xstack=inputs={input_count}:"
            f"layout={layout}:"
            "fill=black:"
            "shortest=1"
            "[output_video]"
        )

    filter_complex = ";".join(
        filter_parts
    )

    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[output_video]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-t",
            f"{timeline_duration_seconds:.9f}",
            "-movflags",
            "+faststart",
            str(temporary_output_path),
        ]
    )

    plan["timestamp_overlay"] = {
        "enabled": True,
        "timezone": DISPLAY_TIMEZONE_LABEL,
        "timezone_offset_seconds": (
            DISPLAY_TIMEZONE_OFFSET_SECONDS
        ),
        "display_format": (
            "YYYY-MM-DD HHhMMmSS.mmmS KST"
        ),
        "font_path": (
            None
            if timestamp_font_path is None
            else str(timestamp_font_path)
        ),
        "template_paths": (
            timestamp_template_paths
        ),
    }

    plan["output"] = {
        "path": str(output_path),
        "temporary_path": str(
            temporary_output_path
        ),
        "plan_path": str(plan_path),
        "log_path": str(log_path),
        "tile_width": tile_width,
        "tile_height": tile_height,
        "fps": fps,
        "columns": columns,
        "rows": rows,
        "layout": layout,
        "layout_version": "compact-v1",
        "tiles": tiles,
        "width": max(tile["x"] + tile["width"] for tile in tiles),
        "height": max(tile["y"] + tile["height"] for tile in tiles),
    }

    plan_path.write_text(
        json.dumps(
            plan,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    log_path.write_text(
        (
            "COMMAND\n"
            + " ".join(command)
            + "\n\nSTDOUT\n"
            + result.stdout
            + "\n\nSTDERR\n"
            + result.stderr
        ),
        encoding="utf-8",
    )

    if result.returncode != 0:
        temporary_output_path.unlink(
            missing_ok=True
        )

        error_lines = (
            result.stderr.strip().splitlines()
        )

        error_summary = "\n".join(
            error_lines[-20:]
        )

        raise MergeError(
            "FFmpeg 병합에 실패함.\n"
            f"로그: {log_path}\n"
            f"{error_summary}"
        )

    if (
        not temporary_output_path.exists()
        or temporary_output_path.stat().st_size == 0
    ):
        raise MergeError(
            "FFmpeg는 종료됐지만 결과 영상이 "
            "생성되지 않았거나 크기가 0임."
        )

    if view == "all":
        plan["views"] = {"all": str(output_path)}
        try:
            for variant in ("witness", "own"):
                eligible = [item for item in plan["inputs"] if not item.get("is_placeholder")
                            and bool(item.get("is_accident_vehicle")) == (variant == "own")]
                if not eligible:
                    continue
                result = merge_event(event_id, before_seconds, after_seconds, mode,
                                     tile_width, tile_height, fps, overwrite, revision,
                                     view=variant, _source_plan=plan)
                plan["views"][variant] = result["output"]["path"]
        except Exception:
            temporary_output_path.unlink(missing_ok=True)
            raise

    temporary_output_path.replace(
        output_path
    )

    plan["output"]["file_size_bytes"] = (
        output_path.stat().st_size
    )

    plan_path.write_text(
        json.dumps(
            plan,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return plan
