from typing import Annotated, Any

from fastapi import Depends, HTTPException, status

from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.auth.credentials import RoleACL
from tracecat.auth.enums import AuthType
from tracecat.settings.constants import AUTH_TYPE_TO_SETTING_KEY
from tracecat.settings.service import get_setting
from tracecat.types.auth import Role

WorkspaceUserRole = Annotated[
    Role,
    RoleACL(allow_user=True, allow_service=False, require_workspace="yes"),
]
"""Dependency for a user role for a workspace.

Sets the `ctx_role` context variable.
"""


def require_auth_type_enabled(auth_type: AuthType) -> Any:
    """FastAPI dependency to check if an auth type is enabled."""

    if auth_type not in AUTH_TYPE_TO_SETTING_KEY:
        raise ValueError(f"Invalid auth type: {auth_type}")

    async def _check_auth_type_enabled() -> None:
        # 1. Check that this auth type is allowed
        if auth_type not in config.TRACECAT__AUTH_TYPES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auth type not allowed",
            )
        # 2. Check that the setting is enabled
        key = AUTH_TYPE_TO_SETTING_KEY[auth_type]
        setting = await get_setting(key=key, role=bootstrap_role())
        if setting is None or not isinstance(setting, bool):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid setting configuration",
            )
        if not setting:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Auth type {auth_type} is not enabled",
            )

    return Depends(_check_auth_type_enabled)
