from typing import Annotated

from fastapi import Depends

from tracecat.auth.credentials import (
    authenticate_user_for_workspace,
    authenticate_user_or_service_for_workspace,
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
