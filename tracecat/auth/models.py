import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi_users import schemas
from pydantic import UUID4, BaseModel, EmailStr


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


class UserCreate(schemas.BaseUserCreate):
    first_name: str | None = None
    last_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    role: UserRole | None = None
    first_name: str | None = None
    last_name: str | None = None
    settings: dict[str, Any] | None = None


# === Sessions ===
class SessionRead(BaseModel):
    id: UUID4
    created_at: datetime
    user_id: UUID4
    user_email: EmailStr
