"""Scope helpers for agent presets."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.enums import ScopeSource
from tracecat.db.models import Scope

type PresetScopeAction = Literal["read", "execute", "update", "delete"]

PRESET_SCOPE_ACTIONS: tuple[PresetScopeAction, ...] = (
    "read",
    "execute",
    "update",
    "delete",
)


def preset_scope_name(slug: str, action: PresetScopeAction) -> str:
    """Build canonical scope name for an agent preset."""
    return f"agent:preset:{slug}:{action}"


def preset_scope_names(slug: str) -> dict[PresetScopeAction, str]:
    """Return all canonical scope names for an agent preset."""
    return {action: preset_scope_name(slug, action) for action in PRESET_SCOPE_ACTIONS}


def wildcard_preset_scope_name(action: PresetScopeAction) -> str:
    """Build wildcard preset scope name."""
    return f"agent:preset:*:{action}"


async def ensure_preset_scopes(session: AsyncSession, slugs: Iterable[str]) -> None:
    """Ensure platform preset scopes exist for all provided slugs."""
    values = []
    for slug in set(slugs):
        for action, scope_name in preset_scope_names(slug).items():
            values.append(
                {
                    "name": scope_name,
                    "resource": "agent:preset",
                    "action": action,
                    "description": f"Allow {action} access to agent preset '{slug}'",
                    "source": ScopeSource.PLATFORM,
                    "source_ref": f"agent_preset:{slug}",
                    "organization_id": None,
                }
            )
    if not values:
        return

    stmt = pg_insert(Scope).values(values)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["name"], index_where=Scope.organization_id.is_(None)
    )
    await session.execute(stmt)
