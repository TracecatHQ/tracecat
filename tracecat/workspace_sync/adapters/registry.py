"""Singletons and registries for workspace sync resource adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tracecat.workspace_sync.adapters.agent_preset import AgentPresetAdapter
from tracecat.workspace_sync.adapters.base import ResourceAdapter
from tracecat.workspace_sync.adapters.case_dropdown import CaseDropdownAdapter
from tracecat.workspace_sync.adapters.case_duration import CaseDurationAdapter
from tracecat.workspace_sync.adapters.case_field import CaseFieldAdapter
from tracecat.workspace_sync.adapters.case_tag import CaseTagAdapter
from tracecat.workspace_sync.adapters.secret_metadata import SecretMetadataAdapter
from tracecat.workspace_sync.adapters.skill import SkillAdapter
from tracecat.workspace_sync.adapters.table import TableAdapter
from tracecat.workspace_sync.adapters.variable import VariableAdapter
from tracecat.workspace_sync.adapters.workflow import WorkflowAdapter
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import WorkspaceSpec

WORKFLOW_RESOURCE_ADAPTER = WorkflowAdapter()
AGENT_PRESET_RESOURCE_ADAPTER = AgentPresetAdapter()
SKILL_RESOURCE_ADAPTER = SkillAdapter()
TABLE_RESOURCE_ADAPTER = TableAdapter()
CASE_TAG_RESOURCE_ADAPTER = CaseTagAdapter()
CASE_FIELD_RESOURCE_ADAPTER = CaseFieldAdapter()
CASE_DROPDOWN_RESOURCE_ADAPTER = CaseDropdownAdapter()
CASE_DURATION_RESOURCE_ADAPTER = CaseDurationAdapter()
VARIABLE_RESOURCE_ADAPTER = VariableAdapter()
SECRET_METADATA_RESOURCE_ADAPTER = SecretMetadataAdapter()

# Projection order (mirrors the historical projector method order).
NON_WORKFLOW_RESOURCE_ADAPTERS: tuple[ResourceAdapter, ...] = (
    AGENT_PRESET_RESOURCE_ADAPTER,
    SKILL_RESOURCE_ADAPTER,
    TABLE_RESOURCE_ADAPTER,
    CASE_TAG_RESOURCE_ADAPTER,
    CASE_FIELD_RESOURCE_ADAPTER,
    CASE_DROPDOWN_RESOURCE_ADAPTER,
    CASE_DURATION_RESOURCE_ADAPTER,
    VARIABLE_RESOURCE_ADAPTER,
    SECRET_METADATA_RESOURCE_ADAPTER,
)

# Import order (dependencies first: skills before presets, etc.).
NON_WORKFLOW_IMPORT_ADAPTERS: tuple[ResourceAdapter, ...] = (
    VARIABLE_RESOURCE_ADAPTER,
    SECRET_METADATA_RESOURCE_ADAPTER,
    CASE_TAG_RESOURCE_ADAPTER,
    CASE_DROPDOWN_RESOURCE_ADAPTER,
    CASE_DURATION_RESOURCE_ADAPTER,
    CASE_FIELD_RESOURCE_ADAPTER,
    TABLE_RESOURCE_ADAPTER,
    SKILL_RESOURCE_ADAPTER,
    AGENT_PRESET_RESOURCE_ADAPTER,
)

WORKSPACE_RESOURCE_ADAPTERS: tuple[ResourceAdapter, ...] = (
    WORKFLOW_RESOURCE_ADAPTER,
    *NON_WORKFLOW_RESOURCE_ADAPTERS,
)

RESOURCE_ADAPTERS_BY_TYPE: dict[SyncResourceType, ResourceAdapter] = {
    adapter.resource_type: adapter for adapter in WORKSPACE_RESOURCE_ADAPTERS
}


def workspace_spec_from_maps(
    specs_by_attr: Mapping[str, Mapping[str, Any]],
) -> WorkspaceSpec:
    return WorkspaceSpec.model_validate(
        {
            adapter.spec_attr: dict(
                sorted(specs_by_attr.get(adapter.spec_attr, {}).items())
            )
            for adapter in WORKSPACE_RESOURCE_ADAPTERS
        }
    )
