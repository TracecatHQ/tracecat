from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr

from tracecat.auth.schemas import UserRole
from tracecat.core.schemas import Schema


class AdminUserRead(Schema):
    """Admin view of a user."""

    id: uuid.UUID
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    role: UserRole
    is_active: bool
    is_superuser: bool
    is_verified: bool
    last_login_at: datetime | None = None
