from datetime import datetime

from pydantic import BaseModel, EmailStr

from tracecat.auth.models import UserRole
from tracecat.identifiers import UserID

# Members


class OrgMemberRead(BaseModel):
    user_id: UserID
    first_name: str | None
    last_name: str | None
    email: EmailStr
    role: UserRole
    is_active: bool
    is_superuser: bool
    is_verified: bool
    last_login_at: datetime | None


# Organization


class OrgRead(BaseModel):
    id: str
    name: str
