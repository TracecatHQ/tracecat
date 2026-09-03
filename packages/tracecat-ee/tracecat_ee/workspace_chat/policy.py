"""Entitlement policy helpers for Workspace Chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def require_workspace_chat_entitlement(
    session: AsyncSession,
    role: Role,
) -> None:
    """Require the organization to be entitled to Workspace Chat."""
    await check_entitlement(session, role, Entitlement.WORKSPACE_CHAT)


async def is_workspace_chat_entitled(
    session: AsyncSession,
    role: Role,
) -> bool:
    """Return True if the organization is entitled to Workspace Chat."""
    if role.organization_id is None:
        return False
    return await is_org_entitled(
        session,
        role.organization_id,
        Entitlement.WORKSPACE_CHAT,
    )


async def require_workspace_chat_entitlement_for_entity(
    *,
    session: AsyncSession,
    role: Role,
    entity_type: AgentSessionEntity | str | None,
) -> None:
    """Require Workspace Chat only for Workspace Chat session entities."""
    if entity_type == AgentSessionEntity.WORKSPACE_CHAT:
        await require_workspace_chat_entitlement(session, role)
