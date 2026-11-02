"""Helpers for granting membership in tests.

Org presence is stored: the membership row is written first, then the role
assignment that hangs off it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.membership import ensure_member
from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.models import (
    ExternalGroup,
    ExternalGroupMember,
    ExternalUser,
    Group,
    GroupMember,
    GroupRoleAssignment,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
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
    await ensure_member(session, organization_id, user_id)
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
    await ensure_member(session, organization_id, user_id)
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


async def grant_org_membership_via_group(
    session: AsyncSession,
    *,
    user_id: UserID,
    organization_id: OrganizationID,
) -> Group:
    """Make a user an organization member through a group's org-wide role.

    The role carries no scopes and the grant is indirect, so the user is
    present but unprivileged and the direct org-wide slot stays free.
    """
    role = DBRole(
        name=f"membership-{uuid.uuid4().hex[:8]}",
        slug=None,
        description=None,
        organization_id=organization_id,
    )
    group = Group(
        id=uuid.uuid4(),
        name=f"membership-{uuid.uuid4().hex[:8]}",
        organization_id=organization_id,
    )
    session.add_all([role, group])
    await session.flush()
    await ensure_member(session, organization_id, user_id)
    session.add(
        GroupMember(group_id=group.id, user_id=user_id, organization_id=organization_id)
    )
    session.add(
        GroupRoleAssignment(
            organization_id=organization_id,
            group_id=group.id,
            workspace_id=None,
            role_id=role.id,
        )
    )
    await session.flush()
    return group


async def seed_external_user(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: UserID,
    external_id: str | None = None,
    active: bool = True,
) -> uuid.UUID:
    """Link a user to this organization's identity provider."""
    await session.execute(
        pg_insert(ExternalUser)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            external_id=external_id or f"idp-user-{uuid.uuid4().hex[:10]}",
            active=active,
        )
        .on_conflict_do_nothing(
            index_elements=[ExternalUser.organization_id, ExternalUser.user_id]
        )
    )
    await session.flush()
    return (
        await session.execute(
            select(ExternalUser.id).where(
                ExternalUser.organization_id == organization_id,
                ExternalUser.user_id == user_id,
            )
        )
    ).scalar_one()


async def seed_external_group(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    external_id: str,
    display_name: str | None = None,
) -> ExternalGroup:
    """Create or fetch a synced external group for the organization."""
    stmt = (
        pg_insert(ExternalGroup)
        .values(
            organization_id=organization_id,
            external_id=external_id,
            display_name=display_name or external_id,
        )
        .on_conflict_do_nothing(
            index_elements=[ExternalGroup.organization_id, ExternalGroup.external_id]
        )
    )
    await session.execute(stmt)
    await session.flush()
    select_stmt = select(ExternalGroup).where(
        ExternalGroup.organization_id == organization_id,
        ExternalGroup.external_id == external_id,
    )
    return (await session.execute(select_stmt)).scalar_one()


async def seed_external_group_members(
    session: AsyncSession,
    *,
    external_group_id: uuid.UUID,
    external_user_ids: Sequence[uuid.UUID],
) -> None:
    """Add external users to an external group's shadow member list."""
    if not external_user_ids:
        return
    organization_id = (
        await session.execute(
            select(ExternalGroup.organization_id).where(
                ExternalGroup.id == external_group_id
            )
        )
    ).scalar_one()
    await session.execute(
        pg_insert(ExternalGroupMember)
        .values(
            [
                {
                    "organization_id": organization_id,
                    "external_group_id": external_group_id,
                    "external_user_id": external_user_id,
                }
                for external_user_id in external_user_ids
            ]
        )
        .on_conflict_do_nothing(
            index_elements=[
                ExternalGroupMember.external_group_id,
                ExternalGroupMember.external_user_id,
            ]
        )
    )
    await session.flush()


async def seed_group_member(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: UserID,
) -> None:
    """Add a group_member row."""
    await session.execute(
        pg_insert(GroupMember)
        .values(group_id=group_id, user_id=user_id)
        .on_conflict_do_nothing(
            index_elements=[GroupMember.user_id, GroupMember.group_id]
        )
    )
    await session.flush()
