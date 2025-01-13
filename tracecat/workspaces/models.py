from pydantic import BaseModel, EmailStr, Field

from tracecat import config
from tracecat.auth.models import UserRole
from tracecat.identifiers import OwnerID, UserID, WorkspaceID

# === Workspace === #


# Params
class CreateWorkspaceParams(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    settings: dict[str, str] | None = None
    owner_id: OwnerID = Field(default=config.TRACECAT__DEFAULT_ORG_ID)


class UpdateWorkspaceParams(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    settings: dict[str, str] | None = None


class SearchWorkspacesParams(BaseModel):
    name: str | None = None


# Responses
class WorkspaceMetadataResponse(BaseModel):
    id: WorkspaceID
    name: str
    n_members: int


class WorkspaceMember(BaseModel):
    user_id: UserID
    first_name: str | None
    last_name: str | None
    email: EmailStr
    role: UserRole


class WorkspaceResponse(BaseModel):
    id: WorkspaceID
    name: str
    settings: dict[str, str] | None = None
    owner_id: OwnerID
    n_members: int
    members: list[WorkspaceMember]


# === Membership === #
# Params
class CreateWorkspaceMembershipParams(BaseModel):
    user_id: UserID


# Responses
class WorkspaceMembershipResponse(BaseModel):
    user_id: UserID
    workspace_id: WorkspaceID
