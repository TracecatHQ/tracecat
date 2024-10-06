from typing import Annotated

from fastapi import Depends

from tracecat.auth.credentials import (
    authenticate_service,
    authenticate_user,
    authenticate_user_for_workspace,
    authenticate_user_or_service_for_workspace,
    authenticate_user_or_service_org,
)
from tracecat.types.auth import Role

WorkspaceUserRole = Annotated[Role, Depends(authenticate_user_for_workspace)]
"""Dependency for a user role for a workspace.

Sets the `ctx_role` context variable.
"""

WorkspaceUserOrServiceRole = Annotated[
    Role, Depends(authenticate_user_or_service_for_workspace)
]
"""Dependency for a user or service role for a workspace.

Sets the `ctx_role` context variable.
"""

OrgUserRole = Annotated[Role, Depends(authenticate_user)]
"""Dependency for an organization user role.

Sets the `ctx_role` context variable.
"""

OrgServiceRole = Annotated[Role, Depends(authenticate_service)]
"""Dependency for an organization service role.

Sets the `ctx_role` context variable.
"""

OrgUserOrServiceRole = Annotated[Role, Depends(authenticate_user_or_service_org)]
"""Dependency for an organization user or service role.

Sets the `ctx_role` context variable.
"""
