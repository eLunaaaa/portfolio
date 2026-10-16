from typing import Annotated

from fastapi import (
    Header,
    HTTPException,
    Request,
    status,
)

from app.services.node_auth_service import (
    AuthenticatedNode,
    authenticate_node,
)


def get_authenticated_node(
    request: Request,
    node_id: Annotated[
        str | None,
        Header(
            alias="X-Evidence-Node-ID"
        ),
    ] = None,
    legacy_node_id: Annotated[
        str | None,
        Header(
            alias="X-ACE-Node-ID",
            include_in_schema=False,
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        Header(
            alias="X-Evidence-API-Key"
        ),
    ] = None,
    legacy_api_key: Annotated[
        str | None,
        Header(
            alias="X-ACE-API-Key",
            include_in_schema=False,
        ),
    ] = None,
) -> AuthenticatedNode:
    """
    Pi가 HTTP Header에 넣어 보낸
    노드 ID와 API Key를 검증함.

    필요한 Header:

    X-Evidence-Node-ID:
        Pi 고유 ID임.

    X-Evidence-API-Key:
        해당 Pi에만 발급한 비밀키임.

    검증 성공 시 서버 DB에 매칭된
    node_id와 vehicle_id를 반환함.
    """

    effective_node_id = node_id or legacy_node_id
    effective_api_key = api_key or legacy_api_key

    if (
        effective_node_id is None
        or effective_api_key is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "X-Evidence-Node-ID와 "
                "X-Evidence-API-Key가 필요함."
            ),
            headers={
                "WWW-Authenticate":
                    "Evidence-Node-Key",
            },
        )

    try:
        authenticated = authenticate_node(
            node_id=effective_node_id,
            api_key=effective_api_key,
        )
        # 로그는 요청 header의 주장 ID가 아니라 인증된 신원을 사용한다.
        request.state.authenticated_node = authenticated
        return authenticated

    except (PermissionError, ValueError) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=str(error),
            headers={
                "WWW-Authenticate":
                    "Evidence-Node-Key",
            },
        ) from error
