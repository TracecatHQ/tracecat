"""Helpers for granting membership in tests.

Membership is derived from role assignments, so tests grant a role rather than
insert a membership row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.models import Role as DBRole
from tracecat.db.models import UserRoleAssignment
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID


async def _role_id(
    session: AsyncSession, organization_id: OrganizationID, slug: str
) -> uuid.UUID:
    stmt = select(DBRole.id).where(
        DBRole.organization_id == organization_id,
        DBRole.slug == slug,
    )
    role_id = (await session.execute(stmt)).scalar_one_or_none()
    if role_id is not None:
        return role_id

    # Seeding can collide with a fixture seeding in parallel. A savepoint keeps
    # the collision from discarding the session's other uncommitted state.
    try:
        async with session.begin_nested():
            await seed_system_roles_for_org(session, organization_id)
    except IntegrityError:
        role_id = (await session.execute(stmt)).scalar_one_or_none()
        if role_id is None:
            raise
        return role_id
    return (await session.execute(stmt)).scalar_one()


async def grant_org_membership(
    session: AsyncSession,
    *,
    user_id: UserID,
    organization_id: OrganizationID,
    slug: str = "organization-member",
) -> None:
    """Make a user an organization member by assigning an org-wide role.

    Idempotent: a user may hold at most one org-wide assignment per org, so an
    existing assignment is left in place.
    """
    role_id = await _role_id(session, organization_id, slug)
    await session.execute(
        pg_insert(UserRoleAssignment)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=None,
            role_id=role_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                UserRoleAssignment.organization_id,
                UserRoleAssignment.user_id,
            ],
            index_where=UserRoleAssignment.workspace_id.is_(None),
        )
    )
    await session.flush()


async def grant_workspace_membership(
    session: AsyncSession,
    *,
    user_id: UserID,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    slug: str = "workspace-editor",
) -> None:
    """Make a user a workspace member by assigning a workspace-scoped role.

    Idempotent: a user may hold at most one assignment per workspace.
    """
    role_id = await _role_id(session, organization_id, slug)
    await session.execute(
        pg_insert(UserRoleAssignment)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=workspace_id,
            role_id=role_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                UserRoleAssignment.user_id,
                UserRoleAssignment.workspace_id,
            ],
        )
    )
    await session.flush()
