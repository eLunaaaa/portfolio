from pydantic import (
    BaseModel,
    Field,
)


class AccessVerifyRequest(BaseModel):
    """
    프론트엔드가 조회 코드 검증 API로 보내는 데이터임.
    """

    event_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    access_code: str = Field(
        min_length=8,
        max_length=32,
    )


class AccessVerifyResponse(BaseModel):
    """
    조회 코드 검증 성공 응답임.
    """

    verified: bool
    event_id: str
    session_expires_at: str


class AccessSessionResponse(BaseModel):
    """
    현재 브라우저의 인증 세션 확인 응답임.
    """

    authenticated: bool
    event_id: str
