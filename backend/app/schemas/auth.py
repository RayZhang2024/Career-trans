from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.password_policy import PASSWORD_POLICY


class PasswordPolicyRead(BaseModel):
    version: int
    min_length: int
    max_length: int
    common_passwords_rejected: bool
    composition_requirements: list[str]
    whitespace_allowed: bool

    @classmethod
    def current(cls) -> "PasswordPolicyRead":
        return cls(
            version=PASSWORD_POLICY.version,
            min_length=PASSWORD_POLICY.min_length,
            max_length=PASSWORD_POLICY.max_length,
            common_passwords_rejected=PASSWORD_POLICY.common_passwords_rejected,
            composition_requirements=list(PASSWORD_POLICY.composition_requirements),
            whitespace_allowed=PASSWORD_POLICY.whitespace_allowed,
        )


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str


class ChangePasswordRead(BaseModel):
    changed: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
