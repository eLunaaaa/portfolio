import asyncio
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
import logging
import os
from pathlib import Path
from typing import Literal, cast

from app.db.merge_job_repository import (
    ClaimedMergeJob,
    claim_next_pending_merge_job,
    mark_merge_job_completed,
    mark_merge_job_failed,
    recover_interrupted_merge_jobs,
)
from app.services.collection_service import (
    datetime_to_database_text,
    read_positive_integer,
    refresh_collection_states,
)
from app.services.video_merger import (
    MergeError,
    MergeMode,
    merge_event,
)


logger = logging.getLogger(
    "uvicorn.error"
)


def read_environment_value(environment_name: str) -> str | None:
    """새 환경변수를 우선하고 이전 이름은 한시적으로 호환함."""

    value = os.getenv(environment_name)

    if value is not None:
        return value

    legacy_name = environment_name.replace(
        "EVIDENCE_", "ACE_", 1
    )
    return os.getenv(legacy_name)


WorkerState = Literal[
    "IDLE",
    "COMPLETED",
    "RETRY_SCHEDULED",
    "FAILED",
]


@dataclass(frozen=True)
class MergeWorkerCycleResult:
    """
    Worker 한 번의 실행 결과임.
    """

    state: WorkerState
    event_id: str | None
    attempt_count: int | None
    message: str


@dataclass
class MergeWorkerControl:
    """
    백그라운드 Worker를 중지하기 위해
    필요한 asyncio 객체를 보관함.
    """

    task: asyncio.Task[None]
    stop_event: asyncio.Event


def read_boolean(
    environment_name: str,
    default_value: bool,
) -> bool:
    """
    환경변수의 문자열을 bool로 변환함.

    True:
        1, true, yes, on

    False:
        0, false, no, off
    """

    raw_value = read_environment_value(environment_name)

    if raw_value is None:
        return default_value

    normalized = (
        raw_value.strip().lower()
    )

    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    raise RuntimeError(
        f"{environment_name}은 "
        "true 또는 false여야 함."
    )


def read_nonnegative_float(
    environment_name: str,
    default_value: float,
) -> float:
    """
    환경변수에서 0 이상의 실수를 읽음.
    """

    raw_value = read_environment_value(environment_name)

    if raw_value is None:
        return default_value

    try:
        parsed_value = float(
            raw_value
        )

    except ValueError as error:
        raise RuntimeError(
            f"{environment_name}은 "
            "숫자여야 함."
        ) from error

    if parsed_value < 0:
        raise RuntimeError(
            f"{environment_name}은 "
            "0 이상이어야 함."
        )

    return parsed_value


def read_merge_mode() -> MergeMode:
    """
    자동 병합 모드를 환경변수에서 읽음.

    extended:
        한 영상이라도 존재하는 전체 구간을 사용함.

    common:
        모든 영상이 동시에 존재하는 공통 구간만 사용함.
    """

    raw_mode = (
        read_environment_value("EVIDENCE_MERGE_MODE")
        or "extended"
    ).strip().lower()

    if raw_mode not in {
        "common",
        "extended",
    }:
        raise RuntimeError(
            "EVIDENCE_MERGE_MODE는 common 또는 "
            "extended여야 함."
        )

    return cast(
        MergeMode,
        raw_mode,
    )


MERGE_WORKER_ENABLED = read_boolean(
    "EVIDENCE_MERGE_WORKER_ENABLED",
    True,
)

MERGE_WORKER_POLL_SECONDS = (
    read_positive_integer(
        "EVIDENCE_MERGE_WORKER_POLL_SECONDS",
        2,
    )
)

MERGE_MAXIMUM_ATTEMPTS = (
    read_positive_integer(
        "EVIDENCE_MERGE_MAXIMUM_ATTEMPTS",
        3,
    )
)

MERGE_RETRY_BASE_SECONDS = (
    read_positive_integer(
        "EVIDENCE_MERGE_RETRY_BASE_SECONDS",
        10,
    )
)

MERGE_MODE = read_merge_mode()

MERGE_BEFORE_SECONDS = (
    read_nonnegative_float(
        "EVIDENCE_MERGE_BEFORE_SECONDS",
        10.0,
    )
)

MERGE_AFTER_SECONDS = (
    read_nonnegative_float(
        "EVIDENCE_MERGE_AFTER_SECONDS",
        20.0,
    )
)

MERGE_TILE_WIDTH = (
    read_positive_integer(
        "EVIDENCE_MERGE_TILE_WIDTH",
        640,
    )
)

# 높이를 지정한 기존 설치는 유지하고 새 기본값은 원본 비율 자동 계산을 사용함.
MERGE_TILE_HEIGHT = None if not read_environment_value("EVIDENCE_MERGE_TILE_HEIGHT") else (
    read_positive_integer(
        "EVIDENCE_MERGE_TILE_HEIGHT",
        360,
    )
)

MERGE_FPS = read_positive_integer(
    "EVIDENCE_MERGE_FPS",
    30,
)


def current_utc_text() -> str:
    """
    현재 UTC 시각을 DB 문자열로 반환함.
    """

    return datetime_to_database_text(
        datetime.now(timezone.utc)
    )


def calculate_retry_time(
    attempt_count: int,
) -> str:
    """
    재시도 횟수에 따라 다음 실행 시각을 계산함.

    기본 대기시간이 10초인 경우:

    1회 실패 후:
        10초 뒤 재시도

    2회 실패 후:
        20초 뒤 재시도

    3회 실패:
        최대 횟수이므로 더 이상 재시도하지 않음.
    """

    multiplier = 2 ** max(
        attempt_count - 1,
        0,
    )

    retry_delay_seconds = (
        MERGE_RETRY_BASE_SECONDS
        * multiplier
    )

    retry_time = (
        datetime.now(timezone.utc)
        + timedelta(
            seconds=retry_delay_seconds
        )
    )

    return datetime_to_database_text(
        retry_time
    )


def validate_output_file(
    merge_result: dict,
) -> Path:
    """
    video_merger.merge_event()의 결과에서
    최종 MP4 경로를 읽고 파일 상태를 검사함.

    단순히 FFmpeg 종료 코드만 믿지 않고
    최종 파일이 실제로 존재하며 0바이트가
    아닌지도 다시 확인함.
    """

    output = merge_result.get(
        "output"
    )

    if not isinstance(output, dict):
        raise MergeError(
            "병합 결과에 output 정보가 없음."
        )

    output_path_value = output.get(
        "path"
    )

    if not isinstance(
        output_path_value,
        str,
    ):
        raise MergeError(
            "병합 결과에 영상 경로가 없음."
        )

    output_path = Path(
        output_path_value
    ).resolve()

    if not output_path.is_file():
        raise MergeError(
            "병합 완료 응답은 받았지만 "
            "결과 파일이 없음: "
            f"{output_path}"
        )

    if output_path.stat().st_size <= 0:
        raise MergeError(
            "병합 결과 영상이 0바이트임: "
            f"{output_path}"
        )

    return output_path


def execute_claimed_merge_job(
    job: ClaimedMergeJob,
) -> MergeWorkerCycleResult:
    """
    Worker가 선점한 한 사고를 실제 병합함.

    기존 video_merger.merge_event()를 호출하므로
    수동 병합과 자동 병합이 같은 알고리즘을 사용함.
    """

    try:
        merge_result = merge_event(
            event_id=job.event_id,
            revision=job.target_revision,
            before_seconds=(
                MERGE_BEFORE_SECONDS
            ),
            after_seconds=(
                MERGE_AFTER_SECONDS
            ),
            mode=MERGE_MODE,
            tile_width=MERGE_TILE_WIDTH,
            tile_height=MERGE_TILE_HEIGHT,
            fps=MERGE_FPS,

            # 서버가 병합 중 종료된 뒤 재시도하거나
            # 기존 테스트 결과가 있을 수 있으므로
            # 자동 Worker는 안전하게 다시 생성함.
            overwrite=True,
        )

        output_path = validate_output_file(
            merge_result
        )

        completed_at = (
            current_utc_text()
        )

        mark_merge_job_completed(
            event_id=job.event_id,
            completed_at=completed_at,
            completed_revision=(
                job.target_revision
            ),
        )

        return MergeWorkerCycleResult(
            state="COMPLETED",
            event_id=job.event_id,
            attempt_count=(
                job.attempt_count
            ),
            message=(
                "자동 병합 완료: "
                f"{output_path}"
            ),
        )

    except Exception as error:
        failed_at = current_utc_text()

        final_status = (
            mark_merge_job_failed(
                event_id=job.event_id,
                failed_at=failed_at,
                retry_at=(
                    calculate_retry_time(
                        job.attempt_count
                    )
                ),
                maximum_attempts=(
                    MERGE_MAXIMUM_ATTEMPTS
                ),
                error_message=str(error),
            )
        )

        if final_status == "PENDING":
            return MergeWorkerCycleResult(
                state="RETRY_SCHEDULED",
                event_id=job.event_id,
                attempt_count=(
                    job.attempt_count
                ),
                message=(
                    "병합 실패. 재시도 예정: "
                    f"{error}"
                ),
            )

        return MergeWorkerCycleResult(
            state="FAILED",
            event_id=job.event_id,
            attempt_count=(
                job.attempt_count
            ),
            message=(
                "최대 재시도 횟수를 초과함: "
                f"{error}"
            ),
        )


def run_merge_worker_cycle() -> (
    MergeWorkerCycleResult
):
    """
    Worker의 한 주기를 실행함.

    실행 순서:
    1. 수집 마감 사고를 READY로 전환함.
    2. PENDING 작업 하나를 가져옴.
    3. 작업이 없으면 IDLE을 반환함.
    4. 작업이 있으면 FFmpeg 병합함.
    5. 성공 또는 실패 상태를 DB에 기록함.

    한 주기에서 작업 하나만 처리하여
    영상 여러 개가 동시에 병합되면서
    CPU와 메모리가 급증하는 것을 방지함.
    """

    refresh_result = (
        refresh_collection_states()
    )

    job = claim_next_pending_merge_job(
        current_time=(
            current_utc_text()
        )
    )

    if job is None:
        promoted_count = len(
            refresh_result
            .promoted_event_ids
        )

        return MergeWorkerCycleResult(
            state="IDLE",
            event_id=None,
            attempt_count=None,
            message=(
                "처리할 병합 작업이 없음. "
                "이번 주기 READY 전환: "
                f"{promoted_count}개"
            ),
        )

    return execute_claimed_merge_job(
        job
    )


async def merge_worker_loop(
    stop_event: asyncio.Event,
) -> None:
    """
    FastAPI와 함께 실행되는 백그라운드 루프임.

    FFmpeg와 SQLite 작업은 asyncio.to_thread()로
    별도 Thread에서 실행함.

    따라서 병합 중에도 다음 API를 처리할 수 있음.

    - 영상 업로드
    - heartbeat
    - 사용자 포털
    - 관리자 화면
    """

    logger.info(
        "자동 병합 Worker 시작: "
        "poll=%s초, mode=%s, max_attempts=%s",
        MERGE_WORKER_POLL_SECONDS,
        MERGE_MODE,
        MERGE_MAXIMUM_ATTEMPTS,
    )

    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(
                run_merge_worker_cycle
            )

            if result.state != "IDLE":
                logger.info(
                    "병합 Worker: "
                    "state=%s event_id=%s "
                    "attempt=%s message=%s",
                    result.state,
                    result.event_id,
                    result.attempt_count,
                    result.message,
                )

        except Exception:
            # 특정 작업 오류는 execute_claimed_merge_job이
            # 처리함. 여기까지 온 오류는 DB 연결 등
            # Worker 자체 오류일 가능성이 큼.
            logger.exception(
                "병합 Worker 주기 실행 실패"
            )

        if stop_event.is_set():
            break

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=(
                    MERGE_WORKER_POLL_SECONDS
                ),
            )

        except TimeoutError:
            # 설정된 주기가 지나면 다음 작업을
            # 다시 확인함.
            pass

    logger.info(
        "자동 병합 Worker 종료"
    )


async def start_merge_worker() -> (
    MergeWorkerControl | None
):
    """
    FastAPI 시작 시 자동 Worker를 실행함.

    EVIDENCE_MERGE_WORKER_ENABLED=false라면
    Worker를 실행하지 않음.
    """

    if not MERGE_WORKER_ENABLED:
        logger.info(
            "자동 병합 Worker가 "
            "환경설정으로 비활성화됨."
        )

        return None

    recovered_event_ids = (
        await asyncio.to_thread(
            recover_interrupted_merge_jobs,
            current_utc_text(),
        )
    )

    if recovered_event_ids:
        logger.warning(
            "중단된 병합 작업 복구: %s",
            ", ".join(
                recovered_event_ids
            ),
        )

    stop_event = asyncio.Event()

    task = asyncio.create_task(
        merge_worker_loop(
            stop_event
        ),
        name="evidence-merge-worker",
    )

    return MergeWorkerControl(
        task=task,
        stop_event=stop_event,
    )


async def stop_merge_worker(
    control: MergeWorkerControl | None,
) -> None:
    """
    FastAPI 종료 시 Worker에 종료를 요청함.

    FFmpeg 작업이 실행 중이면 현재 작업이
    끝난 후 Worker가 종료됨.
    """

    if control is None:
        return

    control.stop_event.set()

    await control.task
