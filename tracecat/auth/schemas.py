import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from fastapi_users import schemas
from pydantic import UUID4, BaseModel, ConfigDict, EmailStr, Field, SecretStr


# === Users ===
class UserRole(StrEnum):
    BASIC = "basic"
    ADMIN = "admin"


class UserStatus(StrEnum):
    LIVE = "live"
    INVITED = "invited"
    DEACTIVATED = "deactivated"


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: UserRole
    first_name: str | None = None
    last_name: str | None = None
    settings: dict[str, Any]

    @classmethod
    def system(cls) -> Self:
        return cls(
            id=uuid.uuid4(),
            email="system@tracecat.com",
            role=UserRole.ADMIN,
            first_name="System",
            last_name="",
            settings={},
        )


class UserReadMinimal(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: UserRole
    first_name: str | None = None
    last_name: str | None = None


class UserSearchRead(BaseModel):
    """Identity fields returned by the org-scoped user email search."""

    id: uuid.UUID
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None


class UserCreate(schemas.BaseUserCreate):
    first_name: str | None = None
    last_name: str | None = None
    invitation_token: str | None = None

    def create_update_dict(self) -> dict[str, Any]:
        # Exclude invitation_token from the dict passed to the database
        # since it's not a column on the User model
        d = super().create_update_dict()
        d.pop("invitation_token", None)
        return d


class UserUpdate(schemas.BaseUserUpdate):
    role: UserRole | None = None
    first_name: str | None = None
    last_name: str | None = None
    settings: dict[str, Any] | None = None


class UserSelfUpdate(BaseModel):
    """Profile fields a user may change on their own account.

    Email and password changes go through the dedicated credential endpoints,
    which require the current password.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    settings: dict[str, Any] | None = None


class LoginMethodsRead(BaseModel):
    """Sign-in methods the current user can manage from their account settings."""

    password: bool = Field(
        description="Whether the user can sign in with, and change, a local password"
    )


class PasswordChange(BaseModel):
    current_password: SecretStr = Field(min_length=1)
    new_password: SecretStr = Field(min_length=1)


class EmailChange(BaseModel):
    current_password: SecretStr = Field(min_length=1)
    new_email: EmailStr


# === Sessions ===
class SessionRead(BaseModel):
    id: UUID4
    created_at: datetime
    user_id: UUID4
    user_email: EmailStr
    ip_address: str | None = None
    user_agent: str | None = None
    last_seen_at: datetime | None = None


class ScopeDeniedErrorDetail(BaseModel):
    """Structured explanation of an authorization scope denial."""

    code: Literal["insufficient_scope"]
    message: str
    required_scopes: list[str]
    missing_scopes: list[str]


class ScopeDeniedResponse(BaseModel):
    """HTTP 403 response emitted by the scope-denial exception handler."""

    error: ScopeDeniedErrorDetail
