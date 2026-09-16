"""Organization membership admission.

The membership row is the aggregate root for a user's presence in an
organization; children hang off it by composite foreign key.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Executable, Select, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.db.models import (
    LegacyMembership,
    Organization,
    OrganizationMembership,
)
from tracecat.db.rls import (
    _RLS_CONTEXT_INFO_KEY,
    _apply_rls_context_async,
    _cache_rls_context,
    _RLSContext,
    set_rls_context,
)
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


async def audit_evicted_members(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_ids: Sequence[UUID],
    actor: Role,
) -> None:
    """Emit a removal event for members the database evicted.

    Eviction happens in a deferred trigger at commit, which cannot reach the
    streaming audit sink, so call this after that commit. Users still holding a
    membership row are skipped.

    Args:
        session: Database session used to read post-commit presence.
        organization_id: Organization the users were removed from.
        user_ids: Candidates whose last role path may have been removed.
        actor: Role that performed the removing operation.
    """
    if not user_ids:
        return

    present = set(
        (
            await session.execute(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.user_id.in_(user_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    evicted = [user_id for user_id in dict.fromkeys(user_ids) if user_id not in present]
    if not evicted:
        return

    async with AuditService.with_session(actor, session=session) as audit:
        for user_id in evicted:
            await audit.create_event(
                resource_type="organization_member",
                action="delete",
                resource_id=user_id,
                data={"reason": "last_role_path_removed"},
            )
