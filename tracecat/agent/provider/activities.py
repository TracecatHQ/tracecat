"""Temporal activities exposing custom-provider configuration to the DSL.

These activities live in the OSS layer so the DSL workflow can read
provider-level overrides without depending on the EE schema definitions.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from temporalio import activity

from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentCatalog, AgentCustomProvider


class ResolveCustomProviderOverridesInput(BaseModel):
    """Input for ``resolve_custom_provider_overrides_activity``."""

    role: Role
    catalog_id: uuid.UUID


class CustomProviderOverridesResult(BaseModel):
    """Source-level overrides resolved from an ``AgentCustomProvider`` row.

    All fields are nullable. ``None`` means the source did not configure an
    override at this level; the caller should fall back to the next layer of
    the cascade (action-level override, then Tracecat default).
    """

    system_prompt_replace: str | None = None
    system_prompt_append: str | None = None


@activity.defn
async def resolve_custom_provider_overrides_activity(
    args: ResolveCustomProviderOverridesInput,
) -> CustomProviderOverridesResult:
    """Resolve source-level overrides for the given catalog row.

    Returns an empty ``CustomProviderOverridesResult`` (all ``None``) when:
    - the catalog row is not backed by a custom provider (e.g. platform
      catalog row pointing at a built-in provider);
    - the catalog row exists but the user's organization can't access it
      (defensive — the workflow layer should have already validated this);
    - no row matches at all.

    Errors here must never break the workflow: the cascade gracefully falls
    back to Tracecat defaults.
    """
    activity.logger.info(
        "Resolving custom provider overrides",
        extra={"catalog_id": str(args.catalog_id)},
    )
    async with get_async_session_context_manager() as session:
        stmt = (
            select(
                AgentCustomProvider.system_prompt_replace,
                AgentCustomProvider.system_prompt_append,
            )
            .join(
                AgentCatalog,
                sa.and_(
                    AgentCatalog.organization_id == AgentCustomProvider.organization_id,
                    AgentCatalog.custom_provider_id == AgentCustomProvider.id,
                ),
            )
            .where(AgentCatalog.id == args.catalog_id)
        )
        row = (await session.execute(stmt)).first()
    if row is None:
        return CustomProviderOverridesResult()
    return CustomProviderOverridesResult(
        system_prompt_replace=row.system_prompt_replace,
        system_prompt_append=row.system_prompt_append,
    )
