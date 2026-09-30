"""Bearer-token authentication for the SCIM endpoints (EE).

The provider authenticates with a connection token rather than a user session,
so this builds a ``scim`` role directly instead of going through ``RoleACL``.
The role's authority is fixed here, not stored per connection. The org's IP
allowlist applies, so an org that enforces one must allow its IdP's egress
addresses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from tracecat.auth.api_keys import (
    SCIM_API_KEY_PREFIX,
    parse_managed_api_key,
    verify_api_key,
)
from tracecat.auth.ip_allowlist_enforcement import enforce_org_ip_allowlist
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_auth_context_manager
from tracecat.db.models import ScimConnection
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement

# The only scope the SCIM paths require, via deprovision_user -> delete_member.
SCIM_ROLE_SCOPES = frozenset({"org:member:remove"})

# SCIM clients authenticate with a bearer token, not the session cookie.
UNAUTHORIZED_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Unauthorized",
    headers={"WWW-Authenticate": "Bearer"},
)

scim_bearer_scheme = HTTPBearer(
    scheme_name="ScimConnectionBearer",
    description="Tracecat SCIM connection token.",
    auto_error=False,
)

ScimBearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(scim_bearer_scheme),
]


async def authenticate_scim_connection(
    credentials: ScimBearerCredentialsDep,
) -> Role:
    """Resolve a SCIM connection token into a scim role.

    Raises:
        HTTPException(401): The token is absent, malformed, unknown, revoked,
            fails verification, or its organization is not entitled.
        HTTPException(403): The organization's IP allowlist excludes the client.
    """
    if credentials is None:
        raise UNAUTHORIZED_EXCEPTION
    token = credentials.credentials.strip()
    parsed = parse_managed_api_key(token, prefixes=(SCIM_API_KEY_PREFIX,))
    if parsed is None:
        raise UNAUTHORIZED_EXCEPTION

    async with get_async_session_auth_context_manager() as session:
        stmt = select(ScimConnection).where(ScimConnection.key_id == parsed.key_id)
        connection = (await session.execute(stmt)).scalar_one_or_none()
        if connection is None or connection.revoked_at is not None:
            raise UNAUTHORIZED_EXCEPTION
        if not verify_api_key(token, connection.salt, connection.hashed):
            raise UNAUTHORIZED_EXCEPTION
        if not await is_org_entitled(
            session, connection.organization_id, Entitlement.RBAC_ADDONS
        ):
            raise UNAUTHORIZED_EXCEPTION

        connection_id = connection.id
        organization_id = connection.organization_id

    # The allowlist loader uses the auth pool on a cache miss. Close the token
    # lookup session first so the loader does not acquire a nested auth session.
    await enforce_org_ip_allowlist(organization_id)

    async with get_async_session_auth_context_manager() as session:
        connection = (
            await session.execute(
                select(ScimConnection).where(ScimConnection.id == connection_id)
            )
        ).scalar_one_or_none()
        if (
            connection is None
            or connection.revoked_at is not None
            or connection.key_id != parsed.key_id
        ):
            raise UNAUTHORIZED_EXCEPTION
        connection.last_used_at = datetime.now(UTC)
        await session.commit()

    role = Role(
        type="scim",
        service_id="tracecat-api",
        organization_id=organization_id,
        scim_connection_id=connection_id,
        scopes=SCIM_ROLE_SCOPES,
    )

    ctx_role.set(role)
    return role


ScimConnectionRole = Annotated[Role, Depends(authenticate_scim_connection)]
"""Dependency yielding a scim role for a verified connection token."""
