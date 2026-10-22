"""Recover reviewed mappings without recreating deleted local identities."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from tracecat.db.models import (
    AgentPreset,
    CaseDropdownDefinition,
    CaseDurationDefinition,
    CaseFields,
    CaseTag,
    Secret,
    Skill,
    Table,
    Workflow,
    WorkspaceVariable,
)
from tracecat.workspace_sync.adapters.base import SyncMappingService
from tracecat.workspace_sync.adapters.registry import CASE_FIELD_RESOURCE_ADAPTER
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.types import SyncMappingTarget

_ROW_MODELS = {
    SyncResourceType.WORKFLOW: Workflow,
    SyncResourceType.AGENT_PRESET: AgentPreset,
    SyncResourceType.SKILL: Skill,
    SyncResourceType.TABLE: Table,
    SyncResourceType.CASE_TAG: CaseTag,
    SyncResourceType.CASE_DROPDOWN: CaseDropdownDefinition,
    SyncResourceType.CASE_DURATION: CaseDurationDefinition,
    SyncResourceType.VARIABLE: WorkspaceVariable,
    SyncResourceType.SECRET_METADATA: Secret,
}


async def surviving_mapping_targets(
    service: SyncMappingService, targets: Sequence[SyncMappingTarget]
) -> list[SyncMappingTarget]:
    """Retain reviewed identities that still exist, without reprojecting contents.

    Recovery must finish an already committed Git write even if the workspace
    has since changed. Never adopt a replacement row by name. Shared row locks
    keep surviving targets present until the caller commits its mappings.
    """
    selected: dict[SyncResourceType, set[uuid.UUID]] = {}
    for target in targets:
        selected.setdefault(SyncResourceType(target.resource_type), set()).add(
            target.local_id
        )
    live: set[tuple[str, uuid.UUID]] = set()
    for resource_type, local_ids in selected.items():
        if resource_type == SyncResourceType.CASE_FIELD:
            # Case fields have derived UUIDs inside one schema, not own rows.
            await service.session.execute(
                select(CaseFields.id)
                .where(CaseFields.workspace_id == service.workspace_id)
                .with_for_update(read=True)
            )
            projection = await CASE_FIELD_RESOURCE_ADAPTER.project(service)
            live.update(
                (resource_type, resource.local_id)
                for resource in projection.resources
                if resource.local_id in local_ids
            )
            continue
        model = _ROW_MODELS[resource_type]
        ordered_ids = sorted(local_ids)
        for offset in range(0, len(ordered_ids), 500):
            stmt = select(model.id).where(
                model.workspace_id == service.workspace_id,
                model.id.in_(ordered_ids[offset : offset + 500]),
            )
            if resource_type == SyncResourceType.AGENT_PRESET:
                stmt = stmt.where(AgentPreset.deleted_at.is_(None))
            elif resource_type == SyncResourceType.SKILL:
                stmt = stmt.where(
                    Skill.deleted_at.is_(None), Skill.archived_at.is_(None)
                )
            rows = await service.session.scalars(stmt.with_for_update(read=True))
            live.update((resource_type, local_id) for local_id in rows)
    return [
        target for target in targets if (target.resource_type, target.local_id) in live
    ]
