"""Decide who may approve an agent preset's tool calls."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.controls import has_scope
from tracecat.authz.service import effective_role_ids, query_effective_scopes
from tracecat.db.models import (
    AgentPresetApprover,
    AgentSession,
    Approval,
    effective_group_members,
)
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.identifiers import OrganizationID, WorkspaceID

# Workspace admins and org owners/admins hold it: they may always decide.
APPROVER_OVERRIDE_SCOPE = "workspace:update"
APPROVER_FLOOR_SCOPE = "agent:execute"

UNLINKED_APPROVER_MESSAGE = (
    "Only this agent's approvers can decide its tool calls. "
    "Open the session in Tracecat to approve."
)


class ApproverNotAllowedError(TracecatAuthorizationError):
    """The decider is outside the preset's approver policy."""


def preset_approvers(
    workspace_id: WorkspaceID, preset_id: uuid.UUID
) -> ColumnElement[bool]:
    """Match the preset's listed approvers."""
    return and_(
        AgentPresetApprover.workspace_id == workspace_id,
        AgentPresetApprover.preset_id == preset_id,
    )


async def has_listed_approvers(
    session: AsyncSession, *, workspace_id: WorkspaceID, preset_id: uuid.UUID | None
) -> bool:
    """Return whether the preset lists any approvers; none means the legacy rule."""
    if preset_id is None:
        return False
    listed = exists().where(preset_approvers(workspace_id, preset_id))
    return bool(await session.scalar(select(listed)))


async def ensure_approver_allowed(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    preset_id: uuid.UUID | None,
    approved_by: uuid.UUID | None,
) -> None:
    """Refuse a decider outside the preset's approvers; none listed = legacy rule.

    Listed approvers need ``agent:execute``; ``workspace:update`` always decides.
    The legacy rule adds no scope check, so linking a channel sender never narrows it.

    Raises:
        ApproverNotAllowedError: If the policy does not allow the decider.
    """
    restricted = await has_listed_approvers(
        session, workspace_id=workspace_id, preset_id=preset_id
    )
    if not restricted or preset_id is None:
        return
    if approved_by is None:
        raise ApproverNotAllowedError(UNLINKED_APPROVER_MESSAGE)

    scopes = await query_effective_scopes(
        session, approved_by, organization_id, workspace_id
    )
    if has_scope(scopes, APPROVER_OVERRIDE_SCOPE):
        return
    if not has_scope(scopes, APPROVER_FLOOR_SCOPE):
        raise ApproverNotAllowedError(
            "You need permission to run agents in this workspace to decide "
            "its tool calls."
        )
    held_roles = effective_role_ids(approved_by, organization_id, workspace_id)
    user_groups = select(effective_group_members.c.group_id).where(
        effective_group_members.c.user_id == approved_by
    )
    listed = exists().where(
        preset_approvers(workspace_id, preset_id),
        or_(
            AgentPresetApprover.user_id == approved_by,
            AgentPresetApprover.group_id.in_(user_groups),
            AgentPresetApprover.role_id.in_(held_roles),
        ),
    )
    if not await session.scalar(select(listed)):
        raise ApproverNotAllowedError(
            "Only this agent's approvers can decide its tool calls."
        )


async def ensure_decider_allowed(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    agent_session: AgentSession,
    approvals: Iterable[Approval | None],
    approved_by: uuid.UUID | None,
) -> None:
    """Refuse ``approved_by`` unless each approval's paused run's preset allows it.

    Rows written before runs stamped their preset fall back to the session's.

    Raises:
        ApproverNotAllowedError: If any governing preset refuses the decider.
    """
    preset_ids = {
        approval.agent_preset_id
        if approval is not None and approval.agent_preset_id is not None
        else agent_session.agent_preset_id
        for approval in approvals
    }
    for preset_id in preset_ids:
        await ensure_approver_allowed(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            preset_id=preset_id,
            approved_by=approved_by,
        )
