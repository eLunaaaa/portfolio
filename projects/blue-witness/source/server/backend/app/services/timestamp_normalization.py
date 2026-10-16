from dataclasses import dataclass


# 2020-01-01 00:00:00 UTC
MIN_SUPPORTED_UNIX_SECONDS = 1_577_836_800

# 2100-01-01 00:00:00 UTC
MAX_SUPPORTED_UNIX_SECONDS = 4_102_444_800

# 사고 영상은 현재 10초 안팎이지만 여유를 두어 1시간까지 허용함.
MAX_CLIP_DURATION_NS = 60 * 60 * 1_000_000_000


@dataclass(frozen=True)
class NormalizedEvidenceTimestamps:
    event_time_ns: int
    clip_start_time_ns: int
    clip_end_time_ns: int
    changed: bool
    source_unit: str
    strategy: str


def timestamp_factor_to_ns(value: int) -> tuple[int, str] | None:
    """
    Unix Epoch 값의 자릿수로 단위를 판별함.

    반환 factor를 원본에 곱하면 나노초가 됨.

    예:
        1787233523       -> 초, factor=1_000_000_000
        1787233523708    -> 밀리초, factor=1_000_000
        1787233523708000 -> 마이크로초, factor=1_000
        1787233523708... -> 나노초, factor=1

    2020~2100 범위에 들어오지 않는 값은 부팅시간이나 잘못된
    상대시간일 수 있으므로 추측해서 변환하지 않음.
    """

    candidates = (
        (1, 1_000_000_000, "seconds"),
        (1_000, 1_000_000, "milliseconds"),
        (1_000_000, 1_000, "microseconds"),
        (1_000_000_000, 1, "nanoseconds"),
    )

    for units_per_second, factor, unit_name in candidates:
        unix_seconds = value / units_per_second

        if (
            MIN_SUPPORTED_UNIX_SECONDS
            <= unix_seconds
            <= MAX_SUPPORTED_UNIX_SECONDS
        ):
            return factor, unit_name

    return None


def timestamps_form_valid_clip(
    event_time_ns: int,
    clip_start_time_ns: int,
    clip_end_time_ns: int,
) -> bool:
    """정규화 후보가 정상적인 사고 영상 구간인지 확인함."""

    return (
        clip_start_time_ns
        <= event_time_ns
        <= clip_end_time_ns
        and 0
        <= clip_end_time_ns - clip_start_time_ns
        <= MAX_CLIP_DURATION_NS
    )


def normalize_evidence_timestamps(
    event_time: int,
    clip_start_time: int,
    clip_end_time: int,
) -> NormalizedEvidenceTimestamps:
    """
    업로더 시간을 Unix Epoch 나노초로 통일함.

    우선 각 값의 Epoch 단위를 독립적으로 판별함. 업로더가
    event_time은 밀리초로 만들고 거기에 10초의 나노초 값을
    빼는 혼합 단위 오류를 낸 경우에는 event_time을 먼저
    절대시각으로 변환하고 원본 차이를 나노초 offset으로 적용함.

    인식할 수 없는 작은 값은 기존 테스트·레거시 호환을 위해
    그대로 반환함. 새로 인식된 Epoch 값이 비정상 구간을 만들면
    저장하지 않고 오류를 발생시킴.
    """

    event_result = timestamp_factor_to_ns(event_time)

    if event_result is None:
        return NormalizedEvidenceTimestamps(
            event_time_ns=event_time,
            clip_start_time_ns=clip_start_time,
            clip_end_time_ns=clip_end_time,
            changed=False,
            source_unit="unknown",
            strategy="unchanged",
        )

    event_factor, event_unit = event_result
    normalized_event = event_time * event_factor

    start_result = timestamp_factor_to_ns(clip_start_time)
    end_result = timestamp_factor_to_ns(clip_end_time)

    if start_result is not None and end_result is not None:
        normalized_start = clip_start_time * start_result[0]
        normalized_end = clip_end_time * end_result[0]

        if timestamps_form_valid_clip(
            normalized_event,
            normalized_start,
            normalized_end,
        ):
            return NormalizedEvidenceTimestamps(
                event_time_ns=normalized_event,
                clip_start_time_ns=normalized_start,
                clip_end_time_ns=normalized_end,
                changed=(
                    event_factor != 1
                    or start_result[0] != 1
                    or end_result[0] != 1
                ),
                source_unit=event_unit,
                strategy="absolute-units",
            )

    # 혼합 단위 보정:
    # event_time은 Epoch ms/us이고 영상 전후 offset은 ns인 경우임.
    relative_start = normalized_event + (
        clip_start_time - event_time
    )
    relative_end = normalized_event + (
        clip_end_time - event_time
    )

    if timestamps_form_valid_clip(
        normalized_event,
        relative_start,
        relative_end,
    ):
        return NormalizedEvidenceTimestamps(
            event_time_ns=normalized_event,
            clip_start_time_ns=relative_start,
            clip_end_time_ns=relative_end,
            changed=True,
            source_unit=event_unit,
            strategy="event-epoch-with-ns-offsets",
        )

    raise ValueError(
        "Unix Epoch 시간 단위는 인식했지만 영상 구간이 "
        "1시간을 초과하거나 사고 시각을 포함하지 않음."
    )
