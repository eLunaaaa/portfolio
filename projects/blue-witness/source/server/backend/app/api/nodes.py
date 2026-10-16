from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)

from app.api.node_dependencies import (
    get_authenticated_node,
)
from app.schemas.portal import (
    HeartbeatRequest,
    HeartbeatResponse,
)
from app.services.node_auth_service import (
    AuthenticatedNode,
)
from app.services.portal_service import (
    receive_heartbeat,
)


router = APIRouter(
    prefix="/nodes",
)


@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
)
def heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    authenticated_node: (
        AuthenticatedNode
    ) = Depends(
        get_authenticated_node
    ),
) -> HeartbeatResponse:
    """
    인증된 Raspberry Pi의
    연결 상태를 저장함.

    요청 Body의 vehicle_id와
    API Key에 연결된 vehicle_id가
    일치해야 함.
    """

    requested_vehicle_id = (
        payload.vehicle_id
        .strip()
        .upper()
    )

    if (
        requested_vehicle_id
        != authenticated_node.vehicle_id
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "요청의 vehicle_id가 "
                "인증된 Pi의 차량과 "
                "일치하지 않음."
            ),
        )

    ip_address = None

    if request.client is not None:
        ip_address = (
            request.client.host
        )

    return receive_heartbeat(
        vehicle_id=(
            authenticated_node.vehicle_id
        ),
        ip_address=ip_address,
        software_version=(
            payload.software_version
        ),
    )