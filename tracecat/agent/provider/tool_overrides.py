"""Temporal activity exposing custom-provider tool whitelist to the DSL.

Lives in the OSS layer so the DSL workflow can read the per-source
tools allowlist without depending on the EE schema definitions.
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


class ResolveCustomProviderToolsInput(BaseModel):
    """Input for ``resolve_custom_provider_tools_activity``."""

    role: Role
    catalog_id: uuid.UUID


class CustomProviderToolsResult(BaseModel):
    """Source-level tools allowlist resolved from an ``AgentCustomProvider`` row.

    ``allowed_tools`` is nullable. ``None`` means the source did not
    configure an override at this level; the caller should fall back
    to the next layer of the cascade (action-level override, then SDK
    default toolset). An empty list ``[]`` is a deliberate "disable
    all built-in tools" override.
    """

    allowed_tools: list[str] | None = None


@activity.defn
async def resolve_custom_provider_tools_activity(
    args: ResolveCustomProviderToolsInput,
) -> CustomProviderToolsResult:
    """Resolve source-level tools allowlist for the given catalog row.

    Returns an empty ``CustomProviderToolsResult`` (``allowed_tools=None``)
    when:
    - the catalog row is not backed by a custom provider (e.g. a
      platform catalog row pointing at a built-in provider);
    - no row matches at all;
    - the database query fails for any reason (logged warning, never
      raises — the cascade is best-effort and must not break the
      ``ai.action`` run).
    """
    activity.logger.info(
        "Resolving custom provider tools allowlist",
        extra={"catalog_id": str(args.catalog_id)},
    )
    try:
        async with get_async_session_context_manager() as session:
            stmt = (
                select(AgentCustomProvider.allowed_tools)
                .join(
                    AgentCatalog,
                    sa.and_(
                        AgentCatalog.organization_id
                        == AgentCustomProvider.organization_id,
                        AgentCatalog.custom_provider_id == AgentCustomProvider.id,
                    ),
                )
                .where(AgentCatalog.id == args.catalog_id)
            )
            row = (await session.execute(stmt)).first()
    except Exception as exc:  # noqa: BLE001 - any DB error must fall back to defaults
        activity.logger.warning(
            "Custom provider tools lookup failed; falling back to SDK default",
            extra={
                "catalog_id": str(args.catalog_id),
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return CustomProviderToolsResult()
    if row is None:
        return CustomProviderToolsResult()
    return CustomProviderToolsResult(allowed_tools=row.allowed_tools)
