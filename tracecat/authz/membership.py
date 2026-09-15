"""Organization membership admission.

The membership row is the aggregate root for a user's presence in an
organization; children hang off it by composite foreign key.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    Executable,
    Select,
    Uuid,
    delete,
    func,
    select,
    union_all,
    update,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    AccessToken,
    Group,
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    MCPPersonalAccessToken,
    MCPRefreshToken,
    Organization,
    OrganizationMembership,
    ServiceAccount,
    ServiceAccountApiKey,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.db.rls import (
    _RLS_CONTEXT_INFO_KEY,
    _apply_rls_context_async,
    _cache_rls_context,
    _RLSContext,
    set_rls_context,
    set_rls_context_from_role,
)
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.identifiers import OrganizationID


async def ensure_member(
    session: AsyncSession,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    """Admit a user to an organization, idempotently.

    The membership row is the aggregate root: children hang off it by composite
    foreign key, so it must exist before any assignment or group-member insert.

    Args:
        session: Database session to execute the upsert on.
        organization_id: Organization the user is admitted to.
        user_id: User being admitted.
    """
    await session.execute(
        pg_insert(OrganizationMembership)
        .values(organization_id=organization_id, user_id=user_id)
        .on_conflict_do_nothing(
            index_elements=[
                OrganizationMembership.organization_id,
                OrganizationMembership.user_id,
            ]
        )
    )


async def mirror_workspace_membership(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> None:
    """Mirror a workspace grant into the legacy `membership` table.

    Args:
        session: Database session to execute the upsert on.
        user_id: User gaining workspace access.
        workspace_id: Workspace the user is mirrored into.
    """
    # `membership` is workspace-RLS but written from org context, which has no
    # workspace GUC; the bypass is what lets the statement see its own rows.
    await _with_rls_bypass(
        session,
        pg_insert(LegacyMembership)
        .values(user_id=user_id, workspace_id=workspace_id)
        .on_conflict_do_nothing(
            index_elements=[LegacyMembership.user_id, LegacyMembership.workspace_id]
        ),
    )


async def drop_workspace_membership_mirror(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace_ids: Sequence[UUID] | Select,
) -> None:
    """Drop legacy `membership` mirror rows for a user.

    Args:
        session: Database session to execute the delete on.
        user_id: User losing workspace access.
        workspace_ids: Workspaces to clear, or a subquery selecting them.
    """
    # `membership` is workspace-RLS but written from org context, which has no
    # workspace GUC; without the bypass the delete silently matches no rows.
    await _with_rls_bypass(
        session,
        delete(LegacyMembership).where(
            LegacyMembership.user_id == user_id,
            LegacyMembership.workspace_id.in_(workspace_ids),
        ),
    )


async def _with_rls_bypass(session: AsyncSession, statement: Executable) -> None:
    """Run a statement under the RLS bypass, restoring the caller's context."""
    previous_context = session.sync_session.info.get(_RLS_CONTEXT_INFO_KEY)
    restore_context = (
        previous_context
        if isinstance(previous_context, _RLSContext)
        else _RLSContext(org_id=None, workspace_id=None, user_id=None, bypass=False)
    )
    try:
        await set_rls_context(session, org_id=None, workspace_id=None, bypass=True)
        await session.execute(statement)
    finally:
        # Restore the cache before issuing SQL, including if the transaction failed.
        if previous_context is None:
            session.sync_session.info.pop(_RLS_CONTEXT_INFO_KEY, None)
        else:
            _cache_rls_context(session, restore_context)
        await _apply_rls_context_async(session, restore_context)


BASELINE_ROLE_SLUG = "organization-member"


async def lock_role_changes(
    session: AsyncSession, organization_id: OrganizationID
) -> None:
    """Serialize role-path writes until their transaction commits.

    Organization locking keeps group and direct changes in the same lock order.
    These administrative writes are infrequent; authorization reads do not lock.
    """
    await session.execute(
        select(Organization.id)
        .where(Organization.id == organization_id)
        # NO KEY UPDATE permits FK references (including audit events) while
        # serializing these writers, avoiding lock upgrades against their inserts.
        .with_for_update(key_share=True)
    )


async def remove_member_access(
    session: AsyncSession, *, user: User, organization_id: OrganizationID, actor: Role
) -> None:
    """Clean up after authorized member removal or loss of the final real role.

    Explicit removal is authorized by OrgService; reconciliation reaches this
    only after an authorized role revocation leaves no other real role paths.
    Do not commit the caller's transaction.
    """
    if user.is_superuser:
        raise TracecatAuthorizationError("Cannot delete superuser")
    now = datetime.now(UTC)
    # Key issuance locks the same account row and rejects disabled accounts.
    owned_accounts = (
        await session.scalars(
            select(ServiceAccount.id)
            .where(
                ServiceAccount.organization_id == organization_id,
                ServiceAccount.owner_user_id == user.id,
            )
            .order_by(ServiceAccount.id)
            .with_for_update()
        )
    ).all()
    if owned_accounts:
        await session.execute(
            update(ServiceAccount)
            .where(
                ServiceAccount.id.in_(owned_accounts),
                ServiceAccount.disabled_at.is_(None),
            )
            .values(disabled_at=now)
        )
        await session.execute(
            update(ServiceAccountApiKey)
            .where(
                ServiceAccountApiKey.service_account_id.in_(owned_accounts),
                ServiceAccountApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_by=actor.user_id)
        )
    await session.execute(
        delete(AccessToken).where(cast(Any, AccessToken.user_id) == user.id)
    )
    await session.execute(
        update(MCPRefreshToken)
        .where(
            MCPRefreshToken.user_id == user.id,
            MCPRefreshToken.organization_id == organization_id,
            MCPRefreshToken.status != "revoked",
        )
        .values(status="revoked")
    )
    await session.execute(
        update(MCPPersonalAccessToken)
        .where(
            MCPPersonalAccessToken.user_id == user.id,
            MCPPersonalAccessToken.organization_id == organization_id,
            MCPPersonalAccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_by=actor.user_id)
    )
    workspace_ids = select(Workspace.id).where(
        Workspace.organization_id == organization_id
    )
    group_ids = select(Group.id).where(Group.organization_id == organization_id)
    await session.execute(
        delete(LegacyMembership).where(
            LegacyMembership.user_id == user.id,
            LegacyMembership.workspace_id.in_(workspace_ids),
        )
    )
    await session.execute(
        delete(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user.id,
            UserRoleAssignment.organization_id == organization_id,
        )
    )
    await session.execute(
        delete(GroupMember).where(
            GroupMember.user_id == user.id, GroupMember.group_id.in_(group_ids)
        )
    )
    await session.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == organization_id,
        )
    )


async def reconcile_member_access(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_ids: Iterable[UUID],
    actor: Role,
    remove_if_empty: bool = False,
) -> None:
    """Reconcile all organization paths after an authorized role mutation."""
    if actor.workspace_id is not None:
        await set_rls_context(
            session,
            org_id=organization_id,
            workspace_id=None,
            user_id=actor.user_id,
            bypass=False,
        )
    try:
        await _reconcile_member_access(
            session,
            organization_id=organization_id,
            user_ids=user_ids,
            actor=actor,
            remove_if_empty=remove_if_empty,
        )
    finally:
        if actor.workspace_id is not None:
            await set_rls_context_from_role(session, actor)


async def _reconcile_member_access(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_ids: Iterable[UUID],
    actor: Role,
    remove_if_empty: bool = False,
) -> None:
    """Preserve workspace users' baseline, or revoke users losing their final role.

    Call after flushing role changes while holding ``lock_role_changes``.
    A baseline is support for other roles, never a remaining role for this check.
    Admission flows may still create standalone baseline members during expand.
    """
    ids = set(user_ids)
    if not ids:
        return
    await session.flush()
    paths = union_all(
        select(
            UserRoleAssignment.user_id,
            UserRoleAssignment.workspace_id,
            DBRole.slug,
        )
        .join(DBRole, DBRole.id == UserRoleAssignment.role_id)
        .where(
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.user_id.in_(ids),
        ),
        select(GroupMember.user_id, GroupRoleAssignment.workspace_id, DBRole.slug)
        .join_from(
            GroupMember,
            GroupRoleAssignment,
            GroupMember.group_id == GroupRoleAssignment.group_id,
        )
        .join(DBRole, DBRole.id == GroupRoleAssignment.role_id)
        .where(
            GroupRoleAssignment.organization_id == organization_id,
            GroupMember.user_id.in_(ids),
        ),
    ).subquery()
    states = (
        (
            await session.execute(
                select(
                    paths.c.user_id,
                    func.bool_or(paths.c.slug.is_distinct_from(BASELINE_ROLE_SLUG)),
                    func.bool_or(paths.c.workspace_id.is_(None)),
                ).group_by(paths.c.user_id)
            )
        )
        .tuples()
        .all()
    )
    real_role_users = {user_id for user_id, real, _ in states if real}
    org_role_users = {user_id for user_id, _, org in states if org}
    if remove_if_empty:
        removed_users = (
            (
                await session.execute(
                    select(User).where(
                        sql_cast(User.id, Uuid).in_(ids - real_role_users)
                    )
                )
            )
            .scalars()
            .all()
        )
        for user in removed_users:
            await remove_member_access(
                session, user=user, organization_id=organization_id, actor=actor
            )
    missing_baseline = real_role_users - org_role_users
    if missing_baseline:
        baseline_id = (
            await session.execute(
                select(DBRole.id).where(
                    DBRole.organization_id == organization_id,
                    DBRole.slug == BASELINE_ROLE_SLUG,
                )
            )
        ).scalar_one()
        await session.execute(
            pg_insert(UserRoleAssignment)
            .values(
                [
                    {
                        "organization_id": organization_id,
                        "user_id": user_id,
                        "role_id": baseline_id,
                        "workspace_id": None,
                    }
                    for user_id in missing_baseline
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    UserRoleAssignment.organization_id,
                    UserRoleAssignment.user_id,
                ],
                index_where=UserRoleAssignment.workspace_id.is_(None),
            )
        )
