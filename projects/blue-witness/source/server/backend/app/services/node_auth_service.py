from dataclasses import dataclass
import hashlib
import re
import secrets

from app.db.node_repository import (
    get_node_credential,
    mark_node_authenticated,
    save_node_credential,
)


NODE_ID_PATTERN = re.compile(
    r"^[A-Z0-9_-]{1,64}$"
)


@dataclass(frozen=True)
class AuthenticatedNode:
    """
    인증이 완료된 Pi의 신원임.

    API는 클라이언트가 주장한 vehicle_id보다
    이 객체의 vehicle_id를 신뢰함.
    """

    node_id: str
    vehicle_id: str


@dataclass(frozen=True)
class ProvisionedNode:
    """
    새 Pi 등록 결과임.

    API Key 원문은 등록 시점에만
    CLI로 전달됨.
    """

    node_id: str
    vehicle_id: str

    api_key: str
    api_key_last4: str


def normalize_node_id(
    node_id: str,
) -> str:
    """
    node_id를 대문자로 통일하고
    허용된 형식인지 검사함.
    """

    normalized = (
        node_id.strip().upper()
    )

    if not NODE_ID_PATTERN.fullmatch(
        normalized
    ):
        raise ValueError(
            "node_id는 영문 대문자, 숫자, "
            "밑줄, 하이픈을 사용하여 "
            "1~64자로 입력해야 함."
        )

    return normalized


def hash_node_api_key(
    api_key: str,
) -> str:
    """
    API Key 원문을 SHA-256으로 해시함.

    DB에는 이 함수의 결과만 저장함.
    """

    return hashlib.sha256(
        api_key.encode("utf-8")
    ).hexdigest()


def provision_node(
    node_id: str,
    vehicle_id: str,
    rotate: bool = False,
) -> ProvisionedNode:
    """
    Pi 노드를 차량과 연결하고
    무작위 API Key를 발급함.

    rotate=True이면 기존 키를 교체함.
    키 교체 즉시 기존 키는 무효가 됨.
    """

    normalized_node_id = (
        normalize_node_id(node_id)
    )

    normalized_vehicle_id = (
        vehicle_id.strip().upper()
    )

    if not normalized_vehicle_id:
        raise ValueError(
            "vehicle_id가 필요함."
        )

    raw_api_key = (
        "node_"
        + secrets.token_urlsafe(32)
    )

    api_key_hash = hash_node_api_key(
        raw_api_key
    )

    api_key_last4 = (
        raw_api_key[-4:]
    )

    save_node_credential(
        node_id=normalized_node_id,
        vehicle_id=(
            normalized_vehicle_id
        ),
        api_key_hash=api_key_hash,
        api_key_last4=(
            api_key_last4
        ),
        rotate=rotate,
    )

    return ProvisionedNode(
        node_id=normalized_node_id,
        vehicle_id=(
            normalized_vehicle_id
        ),
        api_key=raw_api_key,
        api_key_last4=(
            api_key_last4
        ),
    )


def authenticate_node(
    node_id: str,
    api_key: str,
) -> AuthenticatedNode:
    """
    Pi가 전송한 node_id와
    API Key를 검증함.

    검증 순서:
    1. node_id 형식을 검사함.
    2. DB에서 등록된 노드를 조회함.
    3. 전달받은 API Key를 해시함.
    4. DB 해시와 constant-time 비교함.
    5. 노드 활성 상태를 확인함.
    6. 서버가 매칭된 차량 ID를 반환함.
    """

    try:
        normalized_node_id = (
            normalize_node_id(node_id)
        )

    except ValueError as error:
        raise PermissionError(
            "노드 인증 정보가 올바르지 않음."
        ) from error

    if not api_key:
        raise PermissionError(
            "노드 인증 정보가 올바르지 않음."
        )

    credential = get_node_credential(
        normalized_node_id
    )

    entered_hash = hash_node_api_key(
        api_key
    )

    expected_hash = (
        credential.api_key_hash
        if credential is not None
        else "0" * 64
    )

    key_matches = (
        secrets.compare_digest(
            entered_hash,
            expected_hash,
        )
    )

    if (
        credential is None
        or not key_matches
    ):
        raise PermissionError(
            "노드 인증 정보가 올바르지 않음."
        )

    if not credential.is_active:
        raise PermissionError(
            "비활성화된 노드임."
        )

    mark_node_authenticated(
        normalized_node_id
    )

    return AuthenticatedNode(
        node_id=credential.node_id,
        vehicle_id=(
            credential.vehicle_id
        ),
    )
