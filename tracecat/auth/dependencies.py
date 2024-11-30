from typing import Annotated

from tracecat.auth.credentials import RoleACL
from tracecat.types.auth import Role

WorkspaceUserRole = Annotated[
    Role,
    RoleACL(allow_user=True, allow_service=False, require_workspace="yes"),
]
"""Dependency for a user role for a workspace.

Sets the `ctx_role` context variable.
"""
