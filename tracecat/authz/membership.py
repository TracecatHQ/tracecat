"""Transactional membership projection for the RBAC compatibility release."""

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from sqlalchemy import Table, delete, literal, select, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    Membership,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)


async def sync_membership(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_ids: Sequence[UUID],
    workspace_id: UUID | None,
) -> None:
    """Mirror an affected scope after assignment changes, before committing.

    The legacy tables remain the read source during the compatibility release.
    Keep a row while any direct or group path survives; remove it with the last
    path. Serialize projections for each user so concurrent revocations cannot
    leave a stale membership behind. Group writers must also lock their group
    before changing its assignments or members.
    """
    if not user_ids:
        return
    await session.flush()
    # NO KEY UPDATE permits the FK locks held by concurrent assignment inserts.
    user_id_column = User.__table__.c.id
    await session.execute(
        select(user_id_column)
        .where(user_id_column.in_(user_ids))
        .order_by(user_id_column)
        .with_for_update(key_share=True)
    )
    present_users = union(
        select(UserRoleAssignment.user_id).where(
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id == workspace_id,
            UserRoleAssignment.user_id.in_(user_ids),
        ),
        select(GroupMember.user_id)
        .join(
            GroupRoleAssignment,
            GroupRoleAssignment.group_id == GroupMember.group_id,
        )
        .where(
            GroupRoleAssignment.organization_id == organization_id,
            GroupRoleAssignment.workspace_id == workspace_id,
            GroupMember.user_id.in_(user_ids),
        ),
    ).subquery("present_users")
    table = cast(
        Table,
        Membership.__table__
        if workspace_id is not None
        else OrganizationMembership.__table__,
    )
    scope_column = "workspace_id" if workspace_id is not None else "organization_id"
    scope_id = workspace_id if workspace_id is not None else organization_id
    await session.execute(
        insert(table)
        .from_select(
            ["user_id", scope_column],
            select(present_users.c.user_id, literal(scope_id)),
        )
        .on_conflict_do_nothing()
    )
    await session.execute(
        delete(table).where(
            table.c[scope_column] == scope_id,
            table.c.user_id.in_(user_ids),
            table.c.user_id.not_in(select(present_users.c.user_id)),
        )
    )
