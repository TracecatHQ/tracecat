"""Bearer-token authentication for the SCIM endpoints (EE).

The provider authenticates with a connection token rather than a user session,
so this builds a ``scim`` role directly instead of going through ``RoleACL``.
The role's authority is fixed here, not stored per connection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from tracecat.auth.api_keys import (
    SCIM_API_KEY_PREFIX,
    parse_managed_api_key,
    verify_api_key,
)
from tracecat.auth.credentials import UNAUTHORIZED_EXCEPTION
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_auth_context_manager
from tracecat.db.models import ScimConnection
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement

# The only scope the SCIM paths require, via deprovision_user -> delete_member.
SCIM_ROLE_SCOPES = frozenset({"org:member:remove"})

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

        connection.last_used_at = datetime.now(UTC)
        session.add(connection)
        await session.commit()

        role = Role(
            type="scim",
            service_id="tracecat-api",
            organization_id=connection.organization_id,
            scim_connection_id=connection.id,
            scopes=SCIM_ROLE_SCOPES,
        )

    ctx_role.set(role)
    return role


ScimConnectionRole = Annotated[Role, Depends(authenticate_scim_connection)]
"""Dependency yielding a scim role for a verified connection token."""
