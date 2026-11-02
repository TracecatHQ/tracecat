"""Shared read-only resolution of the organization's saved default model."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.db.models import AgentCatalog, AgentModelAccess, OrganizationSetting
from tracecat.settings.service import _deserialize_setting_value


async def _read_string_setting(
    session: AsyncSession, organization_id: UUID, key: str
) -> str | None:
    setting = await session.scalar(
        select(OrganizationSetting).where(
            OrganizationSetting.organization_id == organization_id,
            OrganizationSetting.key == key,
        )
    )
    if setting is None:
        return None
    value = _deserialize_setting_value(setting)
    return value if isinstance(value, str) and value else None


async def read_default_model_name(
    session: AsyncSession, organization_id: UUID
) -> str | None:
    """Read the legacy name used when no valid catalog ID is saved."""
    return await _read_string_setting(session, organization_id, "agent_default_model")


async def read_default_model_catalog_id(
    session: AsyncSession, organization_id: UUID
) -> UUID | None:
    """Read a valid catalog ID; malformed IDs allow legacy-name resolution."""
    value = await _read_string_setting(
        session, organization_id, "agent_default_model_catalog_id"
    )
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def resolve_legacy_default_model(
    enabled_models: Sequence[AgentCatalogRead], *, model_name: str
) -> AgentCatalogRead | None:
    """Prefer a unique match, then a unique built-in; reject ambiguous names."""
    matches = [entry for entry in enabled_models if entry.model_name == model_name]
    if len(matches) == 1:
        return matches[0]
    builtin_matches = [entry for entry in matches if entry.custom_provider_id is None]
    return builtin_matches[0] if len(builtin_matches) == 1 else None


async def resolve_org_default_model(
    session: AsyncSession, organization_id: UUID
) -> AgentCatalogRead | None:
    """Resolve only organization-enabled, tenant-visible catalog entries.

    A valid saved ID is authoritative: a deleted/disabled entry returns None
    rather than reviving a legacy default. Invalid IDs may use the legacy name.
    This internal lookup grants no model access; callers authorize their operation
    and separately apply workspace access rules before using the chosen provider.
    """
    rows = await session.scalars(
        select(AgentCatalog)
        .join(AgentModelAccess, AgentModelAccess.catalog_id == AgentCatalog.id)
        .where(
            AgentModelAccess.organization_id == organization_id,
            AgentModelAccess.workspace_id.is_(None),
            or_(
                AgentCatalog.organization_id == organization_id,
                AgentCatalog.organization_id.is_(None),
            ),
        )
    )
    enabled_models = [AgentCatalogRead.model_validate(row) for row in rows]
    if catalog_id := await read_default_model_catalog_id(session, organization_id):
        return next((entry for entry in enabled_models if entry.id == catalog_id), None)
    if model_name := await read_default_model_name(session, organization_id):
        return resolve_legacy_default_model(enabled_models, model_name=model_name)
    return None
