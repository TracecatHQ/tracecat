"""Lifecycle of an organization's SCIM connection token (EE).

One connection per organization, enforced by a unique constraint. Rotation
replaces the credential material on the existing row, so the previous token
stops verifying immediately.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.audit.logger import audit_log
from tracecat.auth.api_keys import (
    SCIM_API_KEY_PREFIX,
    generate_managed_api_key,
    make_api_key_preview,
)
from tracecat.authz.controls import has_scope, require_scope
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import lock_role_changes
from tracecat.authz.service import resolve_granter_scopes
from tracecat.db.models import ScimConnection
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.service import BaseOrgService

SCIM_TOKEN_SCOPE = "org:scim:token"


class ScimTokenOwnerRequiredError(TracecatAuthorizationError):
    """Raised when someone other than an organization owner issues the token."""


class IssuedScimConnection:
    """A connection paired with its raw token, which is shown only once."""

    __slots__ = ("connection", "token")

    def __init__(self, connection: ScimConnection, token: str) -> None:
        self.connection = connection
        self.token = token

    @property
    def connection_id(self) -> uuid.UUID:
        return self.connection.id


class ScimConnectionService(BaseOrgService):
    """Issues, reads, and revokes the organization's SCIM connection token."""

    service_name = "scim_connection"

    # The token can add anyone to any mapped group, so only owners may issue
    # or rotate it. Admins manage mappings; memberships come from the IdP.
    @require_scope("org:scim:manage")
    @audit_log(
        resource_type="scim_connection",
        action="create",
        resource_id_attr="connection_id",
    )
    async def issue_token(self) -> IssuedScimConnection:
        """Create the organization's connection, or rotate an existing one.

        Returns:
            The stored connection and its raw token.

        Raises:
            ScimTokenOwnerRequiredError: The caller is not an organization owner.
        """
        # Serializes against role changes, so a concurrent demotion cannot land
        # between the owner check and the new token.
        await lock_role_changes(self.session, self.organization_id)
        await self._ensure_can_issue_token()
        generated = generate_managed_api_key(prefix=SCIM_API_KEY_PREFIX)
        preview = make_api_key_preview(generated.raw, prefix=SCIM_API_KEY_PREFIX)

        # Upsert, not check-then-insert: concurrent first issues would otherwise
        # race the organization uniqueness constraint. Rotation reuses the row,
        # so the previous token stops verifying at once.
        credential = {
            "key_id": generated.key_id,
            "hashed": generated.hashed,
            "salt": generated.salt_b64,
            "preview": preview,
            "revoked_at": None,
            "last_used_at": None,
            "created_by": self.role.user_id,
        }
        stmt = (
            pg_insert(ScimConnection)
            .values(organization_id=self.organization_id, **credential)
            .on_conflict_do_update(
                index_elements=[ScimConnection.organization_id],
                # A disconnected directory reconnects as pending, so the admin
                # reviews and activates it again. An upsert skips the column's
                # Python-side onupdate.
                set_={
                    **credential,
                    "status": case(
                        (
                            ScimConnection.status == ScimConnectionStatus.DISABLED,
                            ScimConnectionStatus.PENDING,
                        ),
                        else_=ScimConnection.status,
                    ),
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(ScimConnection)
        )
        connection = (await self.session.execute(stmt)).scalar_one()
        await self.session.commit()
        await self.session.refresh(connection)
        return IssuedScimConnection(connection=connection, token=generated.raw)

    @require_scope("org:scim:manage")
    async def get_connection(self) -> ScimConnection:
        """Return the organization's connection status.

        Raises:
            TracecatNotFoundError: No connection has been created.
        """
        connection = await self._get()
        if connection is None:
            raise TracecatNotFoundError("SCIM connection not found")
        return connection

    async def _ensure_can_issue_token(self) -> None:
        """Reject issuing or rotating the token by anyone but an owner.

        Scopes are read live, so a just-demoted owner cannot issue a token from
        a cached snapshot.
        """
        if self.role.is_platform_superuser:
            return
        scopes = await resolve_granter_scopes(self.session, self.role)
        if not has_scope(scopes, SCIM_TOKEN_SCOPE):
            raise ScimTokenOwnerRequiredError(
                "Only organization owners can issue or rotate the SCIM token."
            )

    async def _get(self) -> ScimConnection | None:
        stmt = select(ScimConnection).where(
            ScimConnection.organization_id == self.organization_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
