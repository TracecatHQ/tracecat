from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class Role(BaseModel):
    """The identity of a user or service role.

    Params
    ------
    type : Literal["user", "service"]
        The type of role.
    user_id : str | None
        The user's JWT 'sub' claim, or the service's user_id.
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

    type: Literal["user", "service"]
    workspace_id: UUID | None = None
    user_id: UUID | None = None
    service_id: str
