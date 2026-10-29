"""Private, effective model-catalog state selected by a reviewed pull."""

import uuid

from sqlalchemy import String, or_, select

from tracecat.agent.access.effective import effective_catalog_ids
from tracecat.db.models import AgentCatalog, AgentModelAccess
from tracecat.dsl.enums import PlatformAction
from tracecat.workspace_sync.adapters.base import SyncMappingService
from tracecat.workspace_sync.schemas import WorkspaceRemoteSnapshot


async def catalog_target_state(
    service: SyncMappingService,
    snapshot: WorkspaceRemoteSnapshot,
    *,
    lock: bool = False,
) -> list[tuple[str, str, str, str | None]] | None:
    """Read selected visible/enabled model identities without credential fields.

    Catalog rows and their existing grants stay locked through confirmation.
    Workspace access writers take the same workspace fence as sync, including
    insertion of the first override, which can change inherited model access.
    An effective selection already has an org grant, locked below; uniqueness
    prevents another such grant. Unrelated org inserts cannot revoke it.
    """
    ids = {
        preset.catalog_id
        for preset in snapshot.spec.agent_presets.values()
        if preset.catalog_id is not None
    }
    for workflow in snapshot.spec.workflows.values():
        for action in workflow.definition.actions:
            if action.action not in {PlatformAction.AI_AGENT, PlatformAction.AI_ACTION}:
                continue
            nested = action.args.get("model")
            model = (
                {**action.args, **nested} if isinstance(nested, dict) else action.args
            )
            raw_id = model.get("catalog_id")
            if raw_id is not None:
                try:
                    ids.add(uuid.UUID(str(raw_id)))
                except ValueError:
                    # Dynamic expressions are resolved at execution, not import.
                    continue
    if not ids:
        return None
    visible = or_(
        AgentCatalog.organization_id == service.organization_id,
        AgentCatalog.organization_id.is_(None),
    )
    if lock:
        # Lock parent catalog rows first, matching catalog-delete/cascade order.
        await service.session.execute(
            select(AgentCatalog.id)
            .where(AgentCatalog.id.in_(ids), visible)
            .order_by(AgentCatalog.id)
            .with_for_update(read=True)
        )
        await service.session.execute(
            select(AgentModelAccess.id)
            .where(
                AgentModelAccess.organization_id == service.organization_id,
                AgentModelAccess.catalog_id.in_(ids),
                or_(
                    AgentModelAccess.workspace_id.is_(None),
                    AgentModelAccess.workspace_id == service.workspace_id,
                ),
            )
            .order_by(AgentModelAccess.id)
            .with_for_update(read=True)
        )
    rows = await service.session.execute(
        select(
            AgentCatalog.id.cast(String),
            AgentCatalog.model_provider,
            AgentCatalog.model_name,
            AgentCatalog.custom_provider_id.cast(String),
        )
        .where(
            AgentCatalog.id.in_(ids),
            AgentCatalog.id.in_(
                effective_catalog_ids(service.organization_id, service.workspace_id)
            ),
            visible,
        )
        .order_by(AgentCatalog.id)
    )
    return [
        (catalog_id, provider, name, custom_provider_id)
        for catalog_id, provider, name, custom_provider_id in rows.tuples()
    ]
