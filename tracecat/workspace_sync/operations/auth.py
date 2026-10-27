"""Revalidate the initiating identity against live authorization state."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.service import query_effective_scopes, workspace_membership_exists
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    Scope,
    ServiceAccount,
    User,
)
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement


async def refresh_sync_role(session: AsyncSession, role: Role) -> Role:
    """Reject revoked identities and reload permissions without the request cache."""
    verified_superuser = False
    if role.workspace_id is None or role.organization_id is None:
        raise TracecatAuthorizationError("Workspace access is required")
    if role.type == "service_account":
        if not await is_org_entitled(
            session, role.organization_id, Entitlement.SERVICE_ACCOUNTS
        ):
            raise TracecatAuthorizationError("Service account access was revoked")
        account = await session.scalar(
            select(ServiceAccount)
            .where(
                ServiceAccount.id == role.service_account_id,
                ServiceAccount.organization_id == role.organization_id,
            )
            .options(selectinload(ServiceAccount.scopes))
        )
        if (
            account is None
            or account.disabled_at is not None
            or (
                account.workspace_id is not None
                and account.workspace_id != role.workspace_id
            )
        ):
            raise TracecatAuthorizationError("Service account access was revoked")
        live_scopes = frozenset(scope.name for scope in account.scopes)
    elif role.type == "user" and role.user_id is not None:
        user = await session.get(User, role.user_id)
        if user is None or not user.is_active:
            raise TracecatAuthorizationError("User access was revoked")
        if role.is_platform_superuser and user.is_superuser:
            live_scopes = frozenset({"*"})
            verified_superuser = True
        else:
            membership = await session.scalar(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.user_id == role.user_id,
                    OrganizationMembership.organization_id == role.organization_id,
                )
            )
            if membership is None:
                raise TracecatAuthorizationError("Organization access was revoked")
            live_scopes = await query_effective_scopes(
                session, role.user_id, role.organization_id, role.workspace_id
            )
            if not has_scope(
                live_scopes, "org:workspace:read"
            ) and not await workspace_membership_exists(
                session, user_id=role.user_id, workspace_id=role.workspace_id
            ):
                raise TracecatAuthorizationError("Workspace access was revoked")
    else:
        raise TracecatAuthorizationError("Git sync requires a user or service account")
    if not verified_superuser:
        organization_active = await session.scalar(
            select(Organization.is_active).where(
                Organization.id == role.organization_id
            )
        )
        if not organization_active:
            raise TracecatAuthorizationError("Organization access was revoked")
    if verified_superuser:
        scopes = live_scopes
    else:
        # Intersect effective permissions, not grant spellings: update implies
        # read, and either snapshot may contain a wildcard grant.
        candidates = await session.scalars(
            select(Scope.name).where(
                Scope.organization_id.is_(None)
                | (Scope.organization_id == role.organization_id)
            )
        )
        scopes = frozenset(
            scope
            for scope in candidates
            if "*" not in scope
            and has_scope(role.scopes or frozenset(), scope)
            and has_scope(live_scopes, scope)
        )
    return role.model_copy(
        update={"scopes": scopes, "is_platform_superuser": verified_superuser}
    )
