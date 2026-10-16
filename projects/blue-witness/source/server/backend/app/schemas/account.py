from pydantic import (
    BaseModel,
    Field,
)


class RegisterRequest(BaseModel):
    username: str = Field(
        min_length=4,
        max_length=32,
    )

    password: str = Field(
        min_length=10,
        max_length=128,
    )

    full_name: str = Field(
        min_length=1,
        max_length=64,
    )


class LoginRequest(BaseModel):
    username: str = Field(
        min_length=1,
        max_length=32,
    )

    password: str = Field(
        min_length=1,
        max_length=128,
    )


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)


class AccountResponse(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    created_at: str


class LoginResponse(BaseModel):
    authenticated: bool
    account: AccountResponse
    session_expires_at: str
