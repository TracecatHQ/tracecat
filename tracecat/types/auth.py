from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field

from tracecat.identifiers import InternalServiceID, UserID, WorkspaceID


class AccessLevel(IntEnum):
    """Access control levels for roles."""

    BASIC = 0
    ADMIN = 999


class Role(BaseModel):
    """The identity and authorization of a user or service.

    Params
    ------
    type : Literal["user", "service"]
        The type of role.
    user_id : UUID | None
        The user's ID, or the service's user_id.
        This can be None for internal services, or when a user hasn't been set for the role.
    service_id : str | None = None
        The service's role name, or None if the role is a user.


    User roles
    ----------
    - User roles are authenticated via JWT.
    - The `user_id` is the user's JWT 'sub' claim.
    - User roles do not have an associated `service_id`, this must be None.

    Service roles
    -------------
    - Service roles are authenticated via API key.
    - Used for internal services to authenticate with the API.
    - A service's `user_id` is the user it's acting on behalf of. This can be None for internal services.
    """

    type: Literal["user", "service"] = Field(frozen=True)
    workspace_id: WorkspaceID | None = Field(default=None, frozen=True)
    user_id: UserID | None = Field(default=None, frozen=True)
    access_level: AccessLevel = Field(default=AccessLevel.BASIC, frozen=True)
    service_id: InternalServiceID = Field(frozen=True)
