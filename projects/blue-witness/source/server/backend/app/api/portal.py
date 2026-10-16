from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from fastapi.responses import FileResponse

from app.api.dependencies import (
    get_event_access_session,
    get_current_user,
)
from app.db.account_repository import (
    UserRecord,
)
from app.schemas.portal import (
    EventAccessStatus,
    EventSummary,
    IssuedEventAccessCode,
    UserPortalOverview,
)
from app.services.access_service import (
    issue_access_code,
)
from app.services.portal_access_service import (
    EventAccessDeniedError,
    has_event_access,
    require_event_access,
)
from app.services.portal_service import (
    find_merged_video,
    get_user_event_summary,
    get_user_portal_overview,
)

from pathlib import Path
from typing import Literal
from app.services.portal_service import find_video_view, available_video_views

router = APIRouter(
    prefix="/portal",
)


# 본인인증 후 발급하는 접근 코드의 유효시간임.
# 현재 시연용 정책은 5분임.
ACCESS_CODE_VALID_SECONDS = 5 * 60


def require_owned_event(
    current_user: UserRecord,
    event_id: str,
) -> EventSummary:
    """
    요청한 사고가 현재 로그인 사용자의
    차량에서 발생한 사고인지 검사함.

    로그인만 했다고 모든 사고를 조회할 수 있으면
    다른 사용자의 사고 영상이 노출될 수 있음.

    따라서 사고 상세 조회, 접근 코드 발급,
    영상 조회를 수행하기 전에 이 함수를 호출함.

    성공:
        사용자가 소유한 사고 정보를 반환함.

    실패:
        사고가 없거나 다른 사용자 소유라면
        404 오류를 발생시킴.
    """

    event = get_user_event_summary(
        current_user,
        event_id,
    )

    if event is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "사고를 찾을 수 없거나 "
                "접근 권한이 없음."
            ),
        )

    return event

def resolve_authorized_merged_video(
    event_id: str,
    current_user: UserRecord,
    accident_session: str | None,
    view: str = "all",
) -> tuple[Path, str]:
    """
    병합 영상을 재생하거나 다운로드하기 전에
    필요한 모든 검사를 한 곳에서 수행함.

    검사 순서:
    1. 로그인 사용자가 해당 사고 차량의
       소유자인지 확인함.
    2. 사고 접근 코드 인증 세션을 확인함.
    3. 병합 영상 경로가 존재하는지 확인함.
    4. 실제 파일인지 확인함.
    5. 0바이트 영상인지 확인함.

    재생 API와 다운로드 API가 이 함수를
    함께 사용하므로 보안 규칙이 달라지는 것을
    방지할 수 있음.
    """

    event = require_owned_event(
        current_user,
        event_id,
    )

    try:
        require_event_access(
            accident_session,
            event.event_id,
        )

    except EventAccessDeniedError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=str(error),
        ) from error

    merged_video_result = (find_merged_video(event.event_id) if view == "all"
                           else find_video_view(event.event_id, view))

    if merged_video_result is None:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "병합 영상이 아직 생성되지 않음."
            ),
        )

    video_path, merge_mode = (
        merged_video_result
    )
    if video_path is None:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "병합 영상이 아직 생성되지 않음."
            ),
        )

    resolved_video_path = (
        video_path.resolve()
    )

    if not resolved_video_path.is_file():
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "병합 영상 파일을 찾을 수 없음."
            ),
        )

    if resolved_video_path.stat().st_size <= 0:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "병합 영상 파일이 비어 있음."
            ),
        )

    normalized_merge_mode = (
        merge_mode
        if merge_mode
        else "unknown"
    )

    return (
        resolved_video_path,
        normalized_merge_mode,
    )


@router.get(
    "/overview",
    response_model=UserPortalOverview,
)
def portal_overview(
    current_user: UserRecord = Depends(
        get_current_user
    ),
) -> UserPortalOverview:
    """
    사용자 포털 첫 화면에 필요한 정보를 반환함.

    반환 정보:
    - 현재 로그인 사용자
    - 사용자에게 등록된 차량
    - 사용자 차량에서 발생한 사고 목록

    current_user는 요청 Body에서 받지 않음.

    브라우저의 user_session 쿠키를
    get_current_user()가 검증한 후
    로그인 사용자 정보를 전달함.
    """

    return get_user_portal_overview(
        current_user
    )


@router.get(
    "/events/{event_id}",
    response_model=EventSummary,
)
def portal_event_detail(
    event_id: str,
    current_user: UserRecord = Depends(
        get_current_user
    ),
) -> EventSummary:
    """
    선택한 사고의 상세 정보를 반환함.

    사고 ID만 알고 있다고 조회를 허용하지 않고,
    현재 로그인 사용자가 해당 사고 차량의
    소유자인지도 함께 검사함.
    """

    return require_owned_event(
        current_user,
        event_id,
    )


@router.get(
    "/events/{event_id}/access-status",
    response_model=EventAccessStatus,
)
def portal_event_access_status(
    event_id: str,
    current_user: UserRecord = Depends(
        get_current_user
    ),
    accident_session: str | None = Depends(
        get_event_access_session
    ),
) -> EventAccessStatus:
    """
    선택한 사고 영상의 잠금 상태를 반환함.

    이 API에서는 두 종류의 인증을 검사함.

    user_session:
        아이디와 비밀번호 로그인 상태임.
        get_current_user()가 검사함.

    event_access_session:
        특정 사고의 접근 코드 검증 상태임.
        accident_session 변수로 읽음.

    또한 병합 영상 파일이 실제로 존재하는지도
    함께 확인함.
    """

    # 먼저 로그인 사용자가 해당 사고의
    # 소유자인지 확인함.
    event = require_owned_event(
        current_user,
        event_id,
    )

    # 현재 브라우저가 해당 사고 접근 코드를
    # 이미 통과했는지 확인함.
    access_session = has_event_access(
        accident_session,
        event_id,
    )

    # 해당 사고의 병합 영상이 존재하는지 확인함.
    merged_video_result = find_merged_video(
        event.event_id
    )

    merged_video_path = None

    if merged_video_result is not None:
        merged_video_path, _ = (
            merged_video_result
        )

    merged_video_available = (
        merged_video_path is not None
        and merged_video_path.resolve().is_file()
        and (
            merged_video_path
            .resolve()
            .stat()
            .st_size
            > 0
        )
    )

    return EventAccessStatus(
        event_id=event.event_id,
        unlocked=(
            access_session is not None
        ),
        access_session_expires_at=(
            access_session.expires_at.isoformat(
                timespec="milliseconds"
            )
            if access_session is not None
            else None
        ),
        merged_video_available=(
            merged_video_available
        ),
    )


@router.get("/events/{event_id}/video/modes")
def portal_video_modes(
    event_id: str,
    current_user: UserRecord = Depends(get_current_user),
    accident_session: str | None = Depends(get_event_access_session),
) -> dict:
    """로그인·사고 소유권·접근 코드 인증을 모두 검사한 후 모드 표시 여부를 반환함."""
    resolve_authorized_merged_video(event_id, current_user, accident_session)
    return {"views": available_video_views(event_id)}


@router.get(
    "/events/{event_id}/video",
    response_class=FileResponse,
)
def portal_event_video(
    event_id: str,
    view: Literal["all", "witness", "own"] = "all",
    current_user: UserRecord = Depends(
        get_current_user
    ),
    accident_session: str | None = Depends(
        get_event_access_session
    ),
) -> FileResponse:
    """
    인증된 사용자에게 병합 영상을
    브라우저 재생용으로 반환함.
    """

    video_path, merge_mode = (
        resolve_authorized_merged_video(
            event_id=event_id,
            view=view,
            current_user=current_user,
            accident_session=(
                accident_session
            ),
        )
    )

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        headers={
            "X-Evidence-Merge-Mode": (
                merge_mode
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )


@router.get(
    "/events/{event_id}/video/download",
    response_class=FileResponse,
)
def download_portal_event_video(
    event_id: str,
    view: Literal["all", "witness", "own"] = "all",
    current_user: UserRecord = Depends(
        get_current_user
    ),
    accident_session: str | None = Depends(
        get_event_access_session
    ),
) -> FileResponse:
    """
    인증된 사용자에게 병합 영상을
    첨부파일 형식으로 반환함.

    브라우저의 버튼을 숨기는 것만으로
    다운로드를 보호하지 않음.

    다운로드 요청이 들어올 때마다
    다음 항목을 서버에서 다시 검사함.

    - 사용자 로그인
    - 사고 차량 소유권
    - 사고 접근 코드 인증 세션
    - 병합 영상 파일 상태
    """

    video_path, merge_mode = (
        resolve_authorized_merged_video(
            event_id=event_id,
            view=view,
            current_user=current_user,
            accident_session=(
                accident_session
            ),
        )
    )

    safe_event_id = "".join(
        character
        if (
            character.isalnum()
            or character in "-_"
        )
        else "_"
        for character in event_id
    )

    download_filename = (
        f"{safe_event_id}"
        f"_merged_{merge_mode}_{view}.mp4"
    )

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=download_filename,
        content_disposition_type=(
            "attachment"
        ),
        headers={
            "X-Evidence-Merge-Mode": (
                merge_mode
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )

@router.post(
    "/events/{event_id}/access-code",
    response_model=IssuedEventAccessCode,
)
def issue_owned_event_access_code(
    event_id: str,
    current_user: UserRecord = Depends(
        get_current_user
    ),
) -> IssuedEventAccessCode:
    """
    로그인 사용자가 소유한 사고에 대해
    5분 동안 유효한 접근 코드를 발급함.

    현재 프로토타입에서는 실제 인증기관 대신
    '본인인증이 완료되었다고 가정'한 뒤
    이 API를 호출함.

    발급한 접근 코드의 원문은 이 응답에서만
    브라우저에 전달함.

    DB에는 원본 접근 코드가 아니라
    SHA-256 해시값만 저장됨.
    """

    # 다른 사용자의 사고에 접근 코드를
    # 발급하지 못하도록 소유권을 검사함.
    event = require_owned_event(
        current_user,
        event_id,
    )

    # access_service.issue_access_code()는
    # 만료시간을 시간 단위로 받음.
    #
    # 5분:
    # 300초 / 3600초 = 0.083333...시간
    valid_hours = (
        ACCESS_CODE_VALID_SECONDS
        / 3600
    )

    try:
        # 중요:
        # 접근 코드 발급과 검증을 모두
        # access_service.py에서 수행함.
        #
        # 이 함수가 저장한 해시값을
        # authenticate_access_code()가 검증함.
        issued = issue_access_code(
            event_id=event.event_id,
            valid_hours=valid_hours,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(error),
        ) from error

    return IssuedEventAccessCode(
        event_id=issued.event_id,
        access_code=issued.access_code,
        expires_at=issued.expires_at,
        valid_for_seconds=(
            ACCESS_CODE_VALID_SECONDS
        ),
        message=(
            "본인인증이 완료된 것으로 가정하여 "
            "5분 동안 유효한 접근 코드를 발급함."
        ),
    )
