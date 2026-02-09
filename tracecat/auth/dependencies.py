from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.auth.credentials import RoleACL
from tracecat.auth.enums import AuthType
from tracecat.auth.org_context import resolve_auth_organization_id
from tracecat.auth.types import Role
from tracecat.logger import logger
from tracecat.settings.constants import AUTH_TYPE_TO_SETTING_KEY
from tracecat.settings.service import get_setting, get_setting_override

WorkspaceUserRole = Annotated[
    Role,
    RoleACL(allow_user=True, allow_service=False, require_workspace="yes"),
]
"""Dependency for a user role for a workspace.

Sets the `ctx_role` context variable.
"""


ExecutorWorkspaceRole = Annotated[
    Role,
    RoleACL(
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    ),
]
"""Dependency for an executor role for a workspace.

Sets the `ctx_role` context variable.
"""

ServiceRole = Annotated[
    Role, RoleACL(allow_user=False, allow_service=True, require_workspace="no")
]
"""Dependency for a service role.

Sets the `ctx_role` context variable.
"""

OrgUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]
"""Dependency for a user role at the organization level (no workspace required).

Sets the `ctx_role` context variable.
"""


async def verify_auth_type(auth_type: AuthType, request: Request) -> None:
    """Verify if an auth type is enabled and properly configured.

    Args:
        auth_type: The authentication type to verify

    Raises:
        HTTPException: If the auth type is not allowed or not enabled
        ValueError: If the auth type is invalid
    """

    # 1. Check that this auth type is allowed
    if auth_type not in config.TRACECAT__AUTH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Auth type not allowed",
        )

    if auth_type is AuthType.SAML:
        # 2. Check that SAML is enabled for the selected organization.
        key = AUTH_TYPE_TO_SETTING_KEY[auth_type]
        override = get_setting_override(key)
        if override is not None:
            logger.warning(
                "Overriding auth setting from environment variables. "
                "This is not recommended for production environments.",
                key=key,
                override=override,
            )
            return

        org_id = await resolve_auth_organization_id(request)
        setting = await get_setting(key=key, role=bootstrap_role(org_id))
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
        return

    # OIDC/Google OAuth/basic availability is platform-configured, but org-level
    # SAML enforcement can still block non-SAML methods.
    org_id = await resolve_auth_organization_id(request)
    saml_enforced = await get_setting(
        key="saml_enforced",
        role=bootstrap_role(org_id),
        default=False,
    )
    if saml_enforced is True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SAML authentication is enforced for this organization",
        )


def require_auth_type_enabled(auth_type: AuthType) -> Any:
    """FastAPI dependency to check if an auth type is enabled.

    Args:
        auth_type: The authentication type to check

    Returns:
        FastAPI dependency that verifies the auth type
    """

    if auth_type not in AUTH_TYPE_TO_SETTING_KEY and auth_type not in {
        AuthType.BASIC,
        AuthType.OIDC,
        AuthType.GOOGLE_OAUTH,
    }:
        raise ValueError(f"Invalid auth type: {auth_type}")

    async def _check_auth_type_enabled(request: Request) -> None:
        await verify_auth_type(auth_type, request)

    return Depends(_check_auth_type_enabled)


def require_any_auth_type_enabled(auth_types: Sequence[AuthType]) -> Any:
    """FastAPI dependency to allow any one of the provided auth types."""
    candidate_types = tuple(dict.fromkeys(auth_types))
    if not candidate_types:
        raise ValueError("auth_types must not be empty")

    async def _check_any_auth_type_enabled(request: Request) -> None:
        for auth_type in candidate_types:
            if auth_type in config.TRACECAT__AUTH_TYPES:
                await verify_auth_type(auth_type, request)
                return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Auth type not allowed",
        )

    return Depends(_check_any_auth_type_enabled)
