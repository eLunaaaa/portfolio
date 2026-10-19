# 파일 역할: 차량 생성·변경 요청의 Pydantic 스키마. API 경계에서 문자열 형식과 필수 항목을 검증한다.
import re
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)


class VehicleCreateRequest(BaseModel):
    """
    사용자가 차량 등록 팝업에서 보내는 데이터임.

    클라이언트가 보내는 필드:
        license_plate:
            실제 차량번호임.

        vehicle_model:
            현대 아이오닉 6과 같은 차종명임.

        display_name:
            사용자가 지정한 차량 별명임.

        icon_key:
            차량 사진 또는 기본 아이콘을 구분하는 키임.

    클라이언트가 보내지 않는 필드:
        user_id:
            로그인 쿠키를 검사하여 서버가 결정함.

        vehicle_id:
            차량 등록 시 서버가 자동 생성함.
    """

    license_plate: str = Field(
        min_length=2,
        max_length=20,
    )

    vehicle_model: str = Field(
        min_length=1,
        max_length=80,
    )

    display_name: str = Field(
        min_length=1,
        max_length=80,
    )

    icon_key: str = Field(
        default="sedan",
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_]*$",
    )

    @field_validator(
        "license_plate"
    )
    @classmethod
    def validate_license_plate(
        cls,
        value: str,
    ) -> str:
        """
        차량번호의 공백을 제거하고
        허용 문자만 포함되었는지 검사함.

        예:
            '12가 3456'
            → '12가3456'
        """

        normalized = "".join(
            value.split()
        ).upper()

        if not re.fullmatch(
            r"[0-9A-Z가-힣-]{2,20}",
            normalized,
        ):
            raise ValueError(
                "차량번호에는 숫자, 영문, "
                "한글, 하이픈만 사용할 수 있음."
            )

        return normalized

    @field_validator(
        "vehicle_model",
        "display_name",
    )
    @classmethod
    def clean_text(
        cls,
        value: str | None,
    ) -> str | None:
        """
        차종과 차량 별명의 앞뒤 공백을 제거하고
        빈 문자열 및 제어문자를 차단함.

        license_plate는 위의 전용 검증기에서
        별도로 정규화하므로 여기서 중복 처리하지 않음.
        """

        if value is None:
            return None

        cleaned = value.strip()

        if not cleaned:
            raise ValueError(
                "빈 값은 사용할 수 없음."
            )

        if any(
            ord(character) < 32
            for character in cleaned
        ):
            raise ValueError(
                "제어문자는 사용할 수 없음."
            )

        return cleaned

    @field_validator(
        "icon_key"
    )
    @classmethod
    def normalize_icon_key(
        cls,
        value: str,
    ) -> str:
        """
        차량 사진·아이콘 키의 앞뒤 공백을 제거하고
        소문자로 통일함.

        실제로 허용되는 키인지는
        vehicle_repository.py의 VALID_ICON_KEYS에서
        다시 검사함.
        """

        return value.strip().lower()


class VehicleResponse(BaseModel):
    """
    차량 등록이 완료된 후 서버가 반환하는 정보임.
    """

    vehicle_id: str
    license_plate: str
    vehicle_model: str | None
    display_name: str | None
    icon_key: str


class VehicleVerificationRequest(BaseModel):
    """실제 인증기관 연결 전 시연용 2단계 확인 입력."""

    purpose: Literal["register", "update", "delete"]
    vehicle_id: str | None = None
    identity_confirmed: bool
    vehicle_confirmed: bool


class VehicleVerificationResponse(BaseModel):
    verification_token: str
    valid_for_seconds: int = 300


class VehicleNicknameRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)

    # 기능: 차량 표시 이름의 공백과 형식을 정규화한다.
    # 입력: 검증 중인 문자열 value와 Pydantic 클래스 cls.
    # 출력: 공통 텍스트 검증을 통과한 문자열.
    # 구조·이유: VehicleCreateRequest.clean_text를 재사용해 생성·수정 요청의 입력 규칙을 맞춘다.
    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        return VehicleCreateRequest.clean_text(value)


class VehicleUpdateRequest(VehicleCreateRequest):
    """등록과 같은 입력 검증을 적용하며 별명을 생략하면 기존 값을 유지함."""

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    icon_key: str = Field(
        min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$"
    )
