from pydantic import BaseModel, Field

from tracecat import config
from tracecat.identifiers import OwnerID, UserID, WorkspaceID

# === Workspace === #


# Params
class CreateWorkspaceParams(BaseModel):
    name: str
    settings: dict[str, str] | None = None
    owner_id: OwnerID = Field(default=config.TRACECAT__DEFAULT_ORG_ID)


class UpdateWorkspaceParams(BaseModel):
    name: str | None = None
    settings: dict[str, str] | None = None


# Responses
class WorkspaceMetadataResponse(BaseModel):
    id: WorkspaceID
    name: str
    n_members: int


class WorkspaceResponse(BaseModel):
    id: WorkspaceID
    name: str
    settings: dict[str, str] | None = None
    owner_id: OwnerID
    n_members: int
    members: list[UserID]


# === Membership === #
# Params
class CreateWorkspaceMembershipParams(BaseModel):
    user_id: UserID


# Responses
class WorkspaceMembershipResponse(BaseModel):
    user_id: UserID
    workspace_id: WorkspaceID
