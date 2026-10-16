import json
import sqlite3
from dataclasses import replace

from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError

from app.api.node_dependencies import (
    get_authenticated_node,
)
from app.db.evidence_repository import (
    DuplicateEvidenceError,
    EventCollectionClosedError,
    EventMetadataConflictError,
    mark_evidence_repair_pending,
)
from app.db.upload_replay_repository import (
    ExistingEvidenceUpload,
    find_existing_evidence_upload,
)
from app.models.evidence import (
    EvidenceMetadata,
    EvidenceStorageStatusResponse,
    EvidenceUploadResponse,
)
from app.services.collection_service import (
    register_evidence_for_collection,
)
from app.services.node_auth_service import (
    AuthenticatedNode,
)
from app.services.storage import (
    EmptyFileError,
    FileChecksumMismatchError,
    FileTooLargeError,
    StoredFile,
    StoredFileMissingError,
    inspect_stored_mp4,
    normalize_sha256,
    remove_stored_file,
    save_mp4_file,
)
from app.services.event_identity_service import (
    EventIdentityConflictError,
    normalize_accident_vehicle_id,
    remove_empty_created_event,
    resolve_or_create_event,
)


router = APIRouter()


def get_content_sha256(
    content_sha256: Annotated[
        str | None,
        Header(alias="X-Evidence-Content-SHA256"),
    ] = None,
    legacy_content_sha256: Annotated[
        str | None,
        Header(
            alias="X-ACE-Content-SHA256",
            include_in_schema=False,
        ),
    ] = None,
) -> str:
    """새 무결성 헤더를 우선하고 기존 헤더도 호환함."""

    value = content_sha256 or legacy_content_sha256
    if value is None:
        raise HTTPException(
            status_code=422,
            detail="X-Evidence-Content-SHA256 헤더가 필요함.",
        )
    return value


@router.get(
    "/events/{event_id}/evidence/status",
    response_model=(
        EvidenceStorageStatusResponse
    ),
)
def get_evidence_storage_status(
    event_id: str,
    content_sha256: Annotated[
        str,
        Depends(get_content_sha256),
    ],
    authenticated_node: Annotated[
        AuthenticatedNode,
        Depends(
            get_authenticated_node
        ),
    ],
) -> EvidenceStorageStatusResponse:
    """
    업로드 결과 응답을 잃어버린 Pi가
    서버의 영구 저장 상태를 확인하는 API임.

    클라이언트가 vehicle_id를 보내게 하지 않고,
    인증된 Node ID와 API Key에 연결된 vehicle_id를
    사용함. 따라서 다른 차량의 저장 여부를 임의로
    조회할 수 없음.

    확인 순서:
    1. 요청 SHA-256 형식을 검사함.
    2. SQLite의 STORED/PENDING evidence와 최초 SHA를 조회함.
    3. 서버 디스크의 MP4 존재·크기를 검사함.
    4. 실제 파일 SHA-256을 다시 계산함.
    5. 파일·부모 디렉터리를 동기화하고 PENDING 복원을 마침.

    200 + storage_ack=true:
        Pi가 재업로드 없이 Queue를 DONE으로 변경함.

    404:
        저장 기록이 없거나 동일 원본 복원이 필요하므로 POST함.
    """

    try:
        normalized_sha256 = (
            normalize_sha256(
                content_sha256
            )
        )

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    existing_upload = (
        find_existing_evidence_upload(
            event_id=event_id,
            vehicle_id=(
                authenticated_node
                .vehicle_id
            ),
        )
    )

    if existing_upload is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "서버에 저장 완료된 해당 "
                "사고 영상이 없음."
            ),
        )

    require_original_hash_match(existing_upload, normalized_sha256)

    try:
        stored_file = inspect_stored_mp4(
            event_id=(
                existing_upload.event_id
            ),
            vehicle_id=(
                existing_upload.vehicle_id
            ),
            expected_sha256=(
                normalized_sha256
            ),
            stored_relative_path=(
                existing_upload.file_path
            ),
        )

    except FileChecksumMismatchError as error:
        if existing_upload.content_sha256 is not None:
            request_original_repair(existing_upload, normalized_sha256)
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=str(error),
        ) from error

    except StoredFileMissingError as error:
        if existing_upload.content_sha256 is not None:
            request_original_repair(existing_upload, normalized_sha256)
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=str(error),
        ) from error

    # DB가 가리키는 파일과 실제 표준 저장 경로가
    # 다르면 무결한 저장 ACK를 반환하지 않음.
    if (
        existing_upload.file_path
        != stored_file.relative_path
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "DB 영상 경로와 실제 저장 "
                "경로가 일치하지 않음."
            ),
        )

    if existing_upload.upload_status == "PENDING":
        existing_upload = complete_original_repair(existing_upload, stored_file)

    return EvidenceStorageStatusResponse(
        stored=True,
        storage_ack=True,
        event_id=existing_upload.event_id,
        vehicle_id=(
            existing_upload.vehicle_id
        ),
        upload_status="STORED",
        file_path=(
            stored_file.relative_path
        ),
        file_size_bytes=(
            stored_file.size_bytes
        ),
        sha256=stored_file.sha256,
        event_status=(
            existing_upload.event_status
        ),
        evidence_count=(
            existing_upload.evidence_count
        ),
        collection_deadline_at=(
            existing_upload
            .collection_deadline_at
        ),
        merge_job_status=(
            existing_upload
            .merge_job_status
        ),
    )


def require_original_hash_match(existing: ExistingEvidenceUpload, sha256: str) -> None:
    if existing.content_sha256 is not None and existing.content_sha256 != sha256:
        raise HTTPException(409, detail="ORIGINAL_HASH_CONFLICT: 최초 원본과 다른 영상임.")


def request_original_repair(existing: ExistingEvidenceUpload, sha256: str) -> None:
    require_original_hash_match(existing, sha256)
    mark_evidence_repair_pending(existing.event_id, existing.vehicle_id, sha256)
    # 기존 송신기의 404 -> POST 복구 경로를 그대로 사용함.
    raise HTTPException(404, detail="ORIGINAL_REPAIR_REQUIRED: 같은 원본을 다시 전송해야 함.")


def complete_original_repair(
    existing: ExistingEvidenceUpload, stored: StoredFile,
) -> ExistingEvidenceUpload:
    metadata = EvidenceMetadata(
        event_id=existing.event_id, vehicle_id=existing.vehicle_id,
        accident_vehicle_id=existing.accident_vehicle_id,
        event_time_ns=existing.event_time_ns,
        clip_start_time_ns=existing.clip_start_time_ns,
        clip_end_time_ns=existing.clip_end_time_ns,
    )
    try:
        register_evidence_for_collection(
            metadata, existing.event_id, stored.relative_path,
            content_sha256=stored.sha256, repair=True,
        )
    except DuplicateEvidenceError:
        # 다른 재시도가 같은 원본을 먼저 복구한 경우 상태를 재확인함.
        pass
    refreshed = find_existing_evidence_upload(existing.event_id, existing.vehicle_id)
    if refreshed is None or refreshed.upload_status != "STORED":
        raise HTTPException(500, detail="원본 복구 상태 기록을 확인할 수 없음.")
    return refreshed


def replay_metadata_matches(
    existing: ExistingEvidenceUpload,
    metadata: EvidenceMetadata,
) -> bool:
    """
    재전송된 메타데이터가 기존 DB 정보와
    동일한 사고 영상인지 확인함.

    단순히 파일명만 같다고 재전송으로
    인정하지 않음.
    """

    return (
        existing.event_id
        == metadata.event_id

        and existing.vehicle_id
        == metadata.vehicle_id

        and existing.accident_vehicle_id
        == metadata.accident_vehicle_id

        and existing.event_time_ns
        == metadata.event_time_ns

        and existing.clip_start_time_ns
        == metadata.clip_start_time_ns

        and existing.clip_end_time_ns
        == metadata.clip_end_time_ns
    )


def build_existing_response(
    existing: ExistingEvidenceUpload,
    stored_file: StoredFile,
) -> EvidenceUploadResponse:
    """
    기존 저장 결과를 Pi에 성공 응답으로 반환함.

    새 레코드를 만들지 않았으므로
    idempotent_replay=True를 반환함.
    """

    return EvidenceUploadResponse(
        saved=True,
        # 기존 파일과 DB 기록을 다시 검증한 뒤
        # Pi가 Queue를 DONE으로 바꿀 수 있도록 ACK함.
        storage_ack=True,
        event_id=existing.event_id,
        vehicle_id=existing.vehicle_id,
        upload_status="STORED",
        file_path=(
            stored_file.relative_path
        ),
        file_size_bytes=(
            stored_file.size_bytes
        ),
        sha256=stored_file.sha256,
        event_status=(
            existing.event_status
        ),
        evidence_count=(
            existing.evidence_count
        ),
        collection_deadline_at=(
            existing
            .collection_deadline_at
        ),
        merge_job_status=(
            existing.merge_job_status
        ),
        idempotent_replay=True,
    )


@router.post(
    "/events/{event_id}/evidence",
    response_model=(
        EvidenceUploadResponse
    ),
    status_code=(
        status.HTTP_201_CREATED
    ),
)
async def upload_evidence(
    event_id: str,
    response: Response,
    metadata: Annotated[
        str,
        Form(),
    ],
    file: Annotated[
        UploadFile,
        File(),
    ],
    content_sha256: Annotated[
        str,
        Depends(get_content_sha256),
    ],
    authenticated_node: Annotated[
        AuthenticatedNode,
        Depends(
            get_authenticated_node
        ),
    ],
) -> EvidenceUploadResponse:
    """
    인증된 Pi가 보낸 MP4와 메타데이터를 저장함.

    신규 업로드:
        HTTP 201
        idempotent_replay=False

    동일 영상 재전송:
        HTTP 200
        idempotent_replay=True
    """

    try:
        parsed_metadata = (
            EvidenceMetadata
            .model_validate_json(
                metadata
            )
        )

    except ValidationError as error:
        await file.close()

        raise HTTPException(
            status_code=422,
            detail=json.loads(
                error.json()
            ),
        ) from error

    if (
        event_id
        != parsed_metadata.event_id
    ):
        await file.close()

        raise HTTPException(
            status_code=400,
            detail=(
                "URL의 event_id와 "
                "metadata의 event_id가 "
                "일치하지 않음."
            ),
        )

    metadata_vehicle_id = (
        parsed_metadata.vehicle_id
        .strip()
        .upper()
    )

    if (
        metadata_vehicle_id
        != authenticated_node.vehicle_id
    ):
        await file.close()

        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "metadata의 vehicle_id가 "
                "인증된 Pi의 차량과 "
                "일치하지 않음."
            ),
        )

    try:
        normalized_accident_vehicle_id = (
            normalize_accident_vehicle_id(
                parsed_metadata.accident_vehicle_id
            )
        )
    except ValueError as error:
        await file.close()
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    parsed_metadata = parsed_metadata.model_copy(
        update={
            "vehicle_id": authenticated_node.vehicle_id,
            "accident_vehicle_id": (
                normalized_accident_vehicle_id
            ),
        }
    )

    original_suffix = Path(
        file.filename or ""
    ).suffix.lower()

    if original_suffix != ".mp4":
        await file.close()

        raise HTTPException(
            status_code=415,
            detail=(
                "MP4 파일만 업로드할 수 있음."
            ),
        )

    if file.content_type != "video/mp4":
        await file.close()

        raise HTTPException(
            status_code=415,
            detail=(
                "Content-Type은 "
                "video/mp4여야 함."
            ),
        )

    try:
        normalized_sha256 = (
            normalize_sha256(
                content_sha256
            )
        )

    except ValueError as error:
        await file.close()

        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    try:
        resolved_identity = resolve_or_create_event(
            event_id=parsed_metadata.event_id,
            accident_vehicle_id=(
                parsed_metadata.accident_vehicle_id
            ),
            event_time_ns=parsed_metadata.event_time_ns,
        )
    except EventIdentityConflictError as error:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except ValueError as error:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    internal_event_id = resolved_identity.event_id

    def cleanup_new_empty_event() -> None:
        """업로드 실패 시 이번 요청이 만든 빈 사건만 정리함."""

        if resolved_identity.created:
            remove_empty_created_event(
                internal_event_id
            )

    existing_upload = (
        find_existing_evidence_upload(
            event_id=(
                parsed_metadata.event_id
            ),
            vehicle_id=(
                authenticated_node
                .vehicle_id
            ),
        )
    )

    # 같은 원본만 재사용/복구함. 사건과 기존 evidence 행은 보존함.
    if existing_upload is not None:
        try:
            if not replay_metadata_matches(existing_upload, parsed_metadata):
                raise HTTPException(409, detail="기존 원본 metadata와 일치하지 않음.")
            require_original_hash_match(existing_upload, normalized_sha256)
            try:
                stored_file = inspect_stored_mp4(
                    existing_upload.event_id, authenticated_node.vehicle_id,
                    normalized_sha256, existing_upload.file_path,
                )
            except (FileChecksumMismatchError, StoredFileMissingError) as error:
                if existing_upload.content_sha256 is None:
                    raise HTTPException(
                        409, detail="최초 검증 SHA가 없는 기존 자료는 자동 교체할 수 없음.",
                    ) from error
                mark_evidence_repair_pending(
                    existing_upload.event_id, existing_upload.vehicle_id,
                    normalized_sha256,
                )
                existing_upload = replace(existing_upload, upload_status="PENDING")
                stored_file = await save_mp4_file(
                    upload_file=file, event_id=internal_event_id,
                    vehicle_id=authenticated_node.vehicle_id,
                    event_time_ns=parsed_metadata.event_time_ns,
                    expected_sha256=normalized_sha256,
                    repair_relative_path=existing_upload.file_path,
                )
            if existing_upload.upload_status == "PENDING":
                existing_upload = complete_original_repair(existing_upload, stored_file)
            response.status_code = status.HTTP_200_OK
            return build_existing_response(existing_upload, stored_file)
        except FileTooLargeError as error:
            raise HTTPException(413, detail=str(error)) from error
        except EmptyFileError as error:
            raise HTTPException(400, detail=str(error)) from error
        except FileChecksumMismatchError as error:
            raise HTTPException(409, detail=str(error)) from error
        finally:
            await file.close()

    try:
        stored_file = await save_mp4_file(
            upload_file=file,
            event_id=(
                internal_event_id
            ),
            vehicle_id=(
                authenticated_node
                .vehicle_id
            ),
            event_time_ns=(
                parsed_metadata.event_time_ns
            ),
            expected_sha256=(
                normalized_sha256
            ),
        )

    except FileTooLargeError as error:
        cleanup_new_empty_event()
        raise HTTPException(
            status_code=413,
            detail=str(error),
        ) from error

    except EmptyFileError as error:
        cleanup_new_empty_event()
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except (
        FileChecksumMismatchError
    ) as error:
        cleanup_new_empty_event()
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except StoredFileMissingError as error:
        cleanup_new_empty_event()
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    try:
        registration = (
            register_evidence_for_collection(
                metadata=parsed_metadata,
                event_id=internal_event_id,
                file_path=(
                    stored_file.relative_path
                ),
                content_sha256=stored_file.sha256,
            )
        )

    except DuplicateEvidenceError as error:
        # 동시에 같은 요청이 들어와 DB 등록을
        # 다른 요청이 먼저 완료한 경우를 처리함.
        existing_upload = (
            find_existing_evidence_upload(
                event_id=(
                    parsed_metadata.event_id
                ),
                vehicle_id=(
                    authenticated_node
                    .vehicle_id
                ),
            )
        )

        if (
            existing_upload is not None
            and replay_metadata_matches(
                existing_upload,
                parsed_metadata,
            )
        ):
            require_original_hash_match(existing_upload, normalized_sha256)
            try:
                # 다른 요청의 등록과 이번 등록 사이에 복원 대기로 바뀌었거나
                # 파일이 손상됐을 수 있으므로 캐시된 파일 정보로 ACK하지 않음.
                stored_file = inspect_stored_mp4(
                    existing_upload.event_id, existing_upload.vehicle_id,
                    normalized_sha256, existing_upload.file_path,
                )
            except (FileChecksumMismatchError, StoredFileMissingError) as storage_error:
                if existing_upload.content_sha256 is not None:
                    mark_evidence_repair_pending(
                        existing_upload.event_id, existing_upload.vehicle_id,
                        normalized_sha256,
                    )
                raise HTTPException(409, detail=str(storage_error)) from storage_error
            if existing_upload.upload_status == "PENDING":
                existing_upload = complete_original_repair(existing_upload, stored_file)
            response.status_code = (
                status.HTTP_200_OK
            )

            return build_existing_response(
                existing=existing_upload,
                stored_file=stored_file,
            )

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except (
        EventMetadataConflictError,
        EventCollectionClosedError,
    ) as error:
        remove_stored_file(
            stored_file
        )

        cleanup_new_empty_event()

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except sqlite3.Error as error:
        # 다른 동시 요청이 같은 파일로 이미 commit/ACK했을 수 있음.
        # 게시한 원본은 보존하여 다음 재시도가 해시를 확인하고 재사용함.
        cleanup_new_empty_event()

        raise HTTPException(
            status_code=500,
            detail=(
                "SQLite 기록에 실패함."
            ),
        ) from error

    return EvidenceUploadResponse(
        saved=True,
        # atomic 파일 저장, SHA-256 확인, DB commit이
        # 모두 성공한 경우에만 이 지점에 도달함.
        storage_ack=True,
        event_id=parsed_metadata.event_id,
        vehicle_id=(
            authenticated_node.vehicle_id
        ),
        upload_status="STORED",
        file_path=(
            stored_file.relative_path
        ),
        file_size_bytes=(
            stored_file.size_bytes
        ),
        sha256=stored_file.sha256,
        event_status=(
            registration.event_status
        ),
        evidence_count=(
            registration.evidence_count
        ),
        collection_deadline_at=(
            registration
            .collection_deadline_at
        ),
        merge_job_status=(
            registration
            .merge_job_status
        ),
        idempotent_replay=(
            stored_file.reused_existing
        ),
    )
