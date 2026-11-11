"""Natural names of a workspace's resources, read straight from the database.

The importer adopts an existing resource whose name matches an incoming one:
a workflow by alias, a preset or skill by slug, a variable or secret by
``(environment, name)``, everything else by name. Rollouts and detach must
know every such name, including unpublished workflows and skills that
workspace sync projections leave out, so they query the tables directly.

Example: ``{("workflow", ("triage",)): <workflow id>, ("variable",
("default", "api_base")): <variable id>}``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from tracecat.workspace_sync.adapters import RESOURCE_ADAPTERS_BY_TYPE
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import WorkflowResourceSpec

type Identity = tuple[str, ...]
type IdentityKey = tuple[str, Identity]
"""``(resource_type, identity)``."""


def spec_identity(resource_type: SyncResourceType, spec: BaseModel) -> Identity | None:
    """Return the name an import would adopt an existing resource by."""
    if isinstance(spec, WorkflowResourceSpec):
        return (spec.alias,) if spec.alias else None
    adapter = RESOURCE_ADAPTERS_BY_TYPE[resource_type]
    if identity := adapter.import_identity(spec):
        return identity
    name = adapter.display_name(spec)
    return (name,) if name else None


def _queries(workspace_id: uuid.UUID) -> list[tuple[SyncResourceType, Select[Any]]]:
    return [
        (
            SyncResourceType.WORKFLOW,
            select(Workflow.id, Workflow.alias).where(
                Workflow.workspace_id == workspace_id, Workflow.alias.is_not(None)
            ),
        ),
        (
            SyncResourceType.AGENT_PRESET,
            select(AgentPreset.id, AgentPreset.slug).where(
                AgentPreset.workspace_id == workspace_id,
                AgentPreset.deleted_at.is_(None),
            ),
        ),
        (
            SyncResourceType.SKILL,
            # Matches the live-slug unique index: archived and deleted skills
            # free their slug, so they cannot clash with an import.
            select(Skill.id, Skill.slug).where(
                Skill.workspace_id == workspace_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            ),
        ),
        (
            SyncResourceType.TABLE,
            select(Table.id, Table.name).where(Table.workspace_id == workspace_id),
        ),
        (
            SyncResourceType.CASE_TAG,
            select(CaseTag.id, CaseTag.name).where(
                CaseTag.workspace_id == workspace_id
            ),
        ),
        (
            SyncResourceType.CASE_DROPDOWN,
            select(CaseDropdownDefinition.id, CaseDropdownDefinition.name).where(
                CaseDropdownDefinition.workspace_id == workspace_id
            ),
        ),
        (
            SyncResourceType.CASE_DURATION,
            select(CaseDurationDefinition.id, CaseDurationDefinition.name).where(
                CaseDurationDefinition.workspace_id == workspace_id
            ),
        ),
        (
            SyncResourceType.VARIABLE,
            select(
                WorkspaceVariable.id,
                WorkspaceVariable.environment,
                WorkspaceVariable.name,
            ).where(WorkspaceVariable.workspace_id == workspace_id),
        ),
        (
            SyncResourceType.SECRET_METADATA,
            select(Secret.id, Secret.environment, Secret.name).where(
                Secret.workspace_id == workspace_id
            ),
        ),
    ]


async def workspace_identities(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    resource_types: Sequence[SyncResourceType] | None = None,
) -> dict[IdentityKey, uuid.UUID]:
    """Return every resource's natural name in a workspace, mapped to its id."""
    wanted = set(resource_types) if resource_types is not None else None
    identities: dict[IdentityKey, uuid.UUID] = {}
    for resource_type, stmt in _queries(workspace_id):
        if wanted is not None and resource_type not in wanted:
            continue
        for local_id, *names in (await session.execute(stmt)).tuples().all():
            identities[(resource_type.value, tuple(str(name) for name in names))] = (
                local_id
            )
    if wanted is None or SyncResourceType.CASE_DROPDOWN in wanted:
        # The dropdown importer also adopts a definition whose ref equals the
        # incoming source id, so refs are identities too, keyed apart from
        # names as ``("ref", ref)``.
        for local_id, ref in (
            await session.execute(
                select(CaseDropdownDefinition.id, CaseDropdownDefinition.ref).where(
                    CaseDropdownDefinition.workspace_id == workspace_id
                )
            )
        ).tuples():
            identities[(SyncResourceType.CASE_DROPDOWN.value, ("ref", ref))] = local_id
    if wanted is None or SyncResourceType.CASE_FIELD in wanted:
        fields = await session.scalar(
            select(CaseFields).where(CaseFields.workspace_id == workspace_id)
        )
        if fields is not None:
            for name in (fields.schema or {}).keys():
                # Same derivation as the case field sync adapter's local ids.
                identities[(SyncResourceType.CASE_FIELD.value, (name,))] = uuid.uuid5(
                    fields.id, name
                )
    return identities
