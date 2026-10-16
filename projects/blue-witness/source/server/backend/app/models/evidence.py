from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.services.timestamp_normalization import (
    normalize_evidence_timestamps,
)


ID_PATTERN = (
    r"^[A-Za-z0-9_-]{1,64}$"
)


class EvidenceMetadata(BaseModel):
    """
    차량이 MP4와 함께 보내는
    사고 메타데이터임.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    event_id: str = Field(
        pattern=ID_PATTERN,
    )

    accident_vehicle_id: str = Field(
        pattern=ID_PATTERN,
    )

    vehicle_id: str = Field(
        pattern=ID_PATTERN,
    )

    event_time_ns: int = Field(
        gt=0,
    )

    clip_start_time_ns: int = Field(
        gt=0,
    )

    clip_end_time_ns: int = Field(
        gt=0,
    )

    time_sync_status: Literal[
        "SYNCED",
        "HOLDOVER",
        "UNRELIABLE",
        "UNKNOWN",
    ] = "UNKNOWN"

    chrony_offset_ms: (
        float | None
    ) = None

    ai_confidence: (
        float | None
    ) = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    @model_validator(
        mode="before"
    )
    @classmethod
    def normalize_unix_epoch_units(
        cls,
        raw_data,
    ):
        """
        초·밀리초·마이크로초·나노초 Epoch를
        서버 표준 Unix Epoch 나노초로 변환함.

        이 단계는 필드 관계 검증보다 먼저 실행되므로
        아래 validate_timestamps()는 항상 정규화된 값을 봄.
        """

        if not isinstance(raw_data, dict):
            return raw_data

        timestamp_fields = (
            "event_time_ns",
            "clip_start_time_ns",
            "clip_end_time_ns",
        )

        if not all(
            field_name in raw_data
            for field_name in timestamp_fields
        ):
            return raw_data

        try:
            normalized = normalize_evidence_timestamps(
                event_time=int(raw_data["event_time_ns"]),
                clip_start_time=int(raw_data["clip_start_time_ns"]),
                clip_end_time=int(raw_data["clip_end_time_ns"]),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"사고 Timestamp 정규화 실패: {error}"
            ) from error

        updated_data = dict(raw_data)
        updated_data["event_time_ns"] = normalized.event_time_ns
        updated_data["clip_start_time_ns"] = normalized.clip_start_time_ns
        updated_data["clip_end_time_ns"] = normalized.clip_end_time_ns

        return updated_data

    @model_validator(
        mode="after"
    )
    def validate_timestamps(
        self,
    ) -> "EvidenceMetadata":
        """
        영상 구간과 사고 시각의 관계를 검증함.
        """

        if (
            self.clip_end_time_ns
            < self.clip_start_time_ns
        ):
            raise ValueError(
                "영상 종료 시각이 "
                "시작 시각보다 빠름."
            )

        if not (
            self.clip_start_time_ns
            <= self.event_time_ns
            <= self.clip_end_time_ns
        ):
            raise ValueError(
                "사고 시각이 영상 구간 "
                "안에 있어야 함."
            )

        return self


class EvidenceUploadResponse(BaseModel):
    """
    서버가 Pi에 반환하는 영상 저장 결과임.

    idempotent_replay=False:
        이번 요청에서 새로 저장함.

    idempotent_replay=True:
        이전에 저장된 동일 영상을 확인하고
        기존 성공 결과를 다시 반환함.

    storage_ack=True:
        MP4의 크기와 SHA-256 확인 및 SQLite 기록까지
        완료했음을 의미함. Pi는 이 값을 확인한 뒤에만
        로컬 Queue를 DONE으로 변경해야 함.
    """

    saved: bool

    # 성공 응답에서 False ACK가 생성되는 실수를
    # Pydantic이 즉시 차단하도록 True만 허용함.
    storage_ack: Literal[True]

    event_id: str
    vehicle_id: str

    upload_status: str

    file_path: str
    file_size_bytes: int
    sha256: str

    event_status: str
    evidence_count: int

    collection_deadline_at: (
        str | None
    )

    merge_job_status: (
        str | None
    )

    idempotent_replay: bool


class EvidenceStorageStatusResponse(BaseModel):
    """
    업로드 응답을 잃어버린 Pi가 조회하는
    서버 영구 저장 확인 결과임.

    storage_ack=True는 다음 조건을 모두 뜻함.

    1. SQLite에 해당 사고·차량 영상이 STORED로 기록됨.
    2. 서버 디스크에 실제 MP4가 존재함.
    3. 실제 MP4의 SHA-256이 Pi가 제시한 값과 일치함.

    Pi는 이 응답을 확인하면 같은 대용량 영상을
    다시 업로드하지 않고 로컬 Queue를 DONE으로
    변경할 수 있음.
    """

    stored: Literal[True]
    storage_ack: Literal[True]

    event_id: str
    vehicle_id: str

    upload_status: Literal["STORED"]

    file_path: str
    file_size_bytes: int
    sha256: str

    event_status: str
    evidence_count: int

    collection_deadline_at: (
        str | None
    )

    merge_job_status: (
        str | None
    )
