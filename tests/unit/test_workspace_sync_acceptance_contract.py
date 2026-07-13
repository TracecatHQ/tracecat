"""Acceptance contract for expanded workspace sync resources.

These tests encode the all-config-resource QA plan for the workspace sync
resource adapter and reconciler implementations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from pydantic import SecretStr, ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer
from tracecat.agent.preset.service import AgentPresetService
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import (
    AgentCatalog,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetSubagent,
    AgentPresetVersion,
    AgentPresetVersionSkill,
    AgentPresetVersionSubagent,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDurationDefinition,
    CaseFields,
    CaseTag,
    CaseTrigger,
    Secret,
    Skill,
    SkillDraftFile,
    SkillVersion,
    Table,
    Webhook,
    Workflow,
    Workspace,
    WorkspaceSyncResourceMapping,
    WorkspaceVariable,
)
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.identifiers.workflow import WF_ID_SHORT_PATTERN, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.sync import PullOptions, PushStatus
from tracecat.tables.schemas import TableUpdate
from tracecat.tables.service import BaseTablesService
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
from tracecat.workspace_sync.adapters import (
    AGENT_PRESET_RESOURCE_ADAPTER,
    TABLE_RESOURCE_ADAPTER,
    WORKSPACE_RESOURCE_ADAPTERS,
)
from tracecat.workspace_sync.adapters.base import VersionedSlug
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.resources import workflow_references
from tracecat.workspace_sync.schemas import (
    AGENT_PRESET_ROOT,
    CASE_DROPDOWN_ROOT,
    CASE_DURATION_ROOT,
    CASE_FIELD_ROOT,
    CASE_TAG_ROOT,
    MANIFEST_FILENAME,
    SECRET_METADATA_ROOT,
    SKILL_ROOT,
    TABLE_ROOT,
    VARIABLE_ROOT,
    WORKFLOW_ROOT,
    AgentPresetSubagentRef,
    ResourceRef,
    SkillFileSpec,
    SkillResourceSpec,
    SkillVersionResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportPreviewRequest,
    WorkspaceSyncExportRequest,
    manifest_resource_roots,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import VcsTreeSnapshot
from tracecat.workspace_sync.workflow import serialize_workflow_spec

EXPANDED_RESOURCE_TYPES = {
    "workflow",
    "agent_preset",
    "skill",
    "table",
    "case_tag",
    "case_field",
    "case_dropdown",
    "case_duration",
    "variable",
    "secret_metadata",
}

EXPECTED_RESOURCE_ROOTS = {
    "workflows": f"{WORKFLOW_ROOT}/",
    "agent_presets": f"{AGENT_PRESET_ROOT}/",
    "skills": f"{SKILL_ROOT}/",
    "tables": f"{TABLE_ROOT}/",
    "case_tags": f"{CASE_TAG_ROOT}/",
    "case_fields": f"{CASE_FIELD_ROOT}/",
    "case_dropdowns": f"{CASE_DROPDOWN_ROOT}/",
    "case_durations": f"{CASE_DURATION_ROOT}/",
    "variables": f"{VARIABLE_ROOT}/",
    "secret_metadata": f"{SECRET_METADATA_ROOT}/",
}


@dataclass(frozen=True)
class LegacyUpgradeHarness:
    """Shared fixture state for the legacy-repo upgrade acceptance tests.

    Each upgrade test drives the same flow: a ``source`` workspace owns the repo
    and publishes the expanded format, while a ``target`` workspace consumes it.
    Both services talk to the same in-memory ``fake_vcs`` server, which was
    seeded at ``legacy_commit_sha`` with the pre-upgrade workflow-only layout.
    """

    git_url: GitUrl
    fake_vcs: FakeVcsServer
    source_service: WorkspaceSyncService
    target_service: WorkspaceSyncService
    target_role: Role
    legacy_commit_sha: str


@pytest.fixture
def workspace_sync_service() -> WorkspaceSyncService:
    session = AsyncMock()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )
    return WorkspaceSyncService(session=session, role=role)


def test_sync_resource_type_declares_expanded_resource_contract() -> None:
    assert {resource_type.value for resource_type in SyncResourceType} >= (
        EXPANDED_RESOURCE_TYPES
    )


def test_resource_adapters_cover_expanded_resource_contract() -> None:
    adapter_types = {
        adapter.resource_type.value for adapter in WORKSPACE_RESOURCE_ADAPTERS
    }

    assert adapter_types >= EXPANDED_RESOURCE_TYPES
    assert len(adapter_types) == len(WORKSPACE_RESOURCE_ADAPTERS)


def test_manifest_declares_expanded_resource_roots() -> None:
    manifest = WorkspaceManifest()

    assert manifest.resources.model_dump(mode="json") == EXPECTED_RESOURCE_ROOTS
    assert manifest_resource_roots(manifest) == tuple(
        root.strip("/") for root in EXPECTED_RESOURCE_ROOTS.values()
    )


def test_manifest_rejects_undeclared_resource_roots() -> None:
    manifest_data = {
        "version": 1,
        "resources": {
            **EXPECTED_RESOURCE_ROOTS,
            "dashboards": "dashboards/",
        },
    }

    with pytest.raises(ValidationError):
        WorkspaceManifest.model_validate(manifest_data)


@pytest.mark.anyio
async def test_parse_files_reports_unsupported_manifest_resource_root(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = {
        MANIFEST_FILENAME: json.dumps(
            {
                "version": 1,
                "resources": {
                    **EXPECTED_RESOURCE_ROOTS,
                    "dashboards": "dashboards/",
                },
            }
        ),
        "dashboards/qa.yml": "version: 1\ntype: dashboard\nid: qa\n",
    }

    snapshot, diagnostics = await workspace_sync_service.parse_files(files)

    assert snapshot.spec.workflows == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].workflow_path == MANIFEST_FILENAME
    assert diagnostics[0].error_type == "parse"
    assert "dashboards" in diagnostics[0].message


def test_full_acceptance_fixture_is_deterministic_and_secret_safe() -> None:
    files = _expanded_full_git_tree(include_schedules=False)

    assert set(files) == _expected_full_paths()
    assert sorted(files) == list(files)
    _assert_secret_metadata_has_no_values(files)
    _assert_variable_exports_have_no_values(files)
    _assert_workflows_have_no_schedules(files)


def test_schedule_fixture_is_opt_in() -> None:
    files_without_schedules = _expanded_full_git_tree(include_schedules=False)
    files_with_schedules = _expanded_full_git_tree(include_schedules=True)
    root_path = f"{WORKFLOW_ROOT}/qa-root/definition.yml"

    assert "schedules" not in yaml.safe_load(files_without_schedules[root_path])
    assert yaml.safe_load(files_with_schedules[root_path])["schedules"] == [
        {
            "status": "offline",
            "cron": "0 8 * * *",
            "timeout": 300,
        }
    ]


def test_selected_acceptance_fixture_models_transitive_closure() -> None:
    files = _expanded_selected_git_tree()

    assert f"{WORKFLOW_ROOT}/qa-root/definition.yml" in files
    assert f"{WORKFLOW_ROOT}/qa-child/definition.yml" in files
    assert f"{WORKFLOW_ROOT}/qa-orphan/definition.yml" not in files
    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml" in files
    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml" in files
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml" in files
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml" in files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml" in files
    assert f"{TABLE_ROOT}/qa_indicators/table.yml" in files
    assert f"{CASE_TAG_ROOT}/qa-alert.yml" in files
    assert f"{VARIABLE_ROOT}/default/qa_config.yml" in files
    assert f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml" in files
    assert not any(path.startswith(f"{CASE_DROPDOWN_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_DURATION_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_FIELD_ROOT}/") for path in files)


def test_workflow_references_detects_preset_agent_preset_arg() -> None:
    workflow = _workflow_spec(
        source_id="qa-agent-workflow",
        title="qa-agent-workflow",
        alias="qa-agent-workflow",
        folder_path="QA/Root",
        actions=[
            {
                "ref": "triage",
                "action": "ai.preset_agent",
                "args": {
                    "preset": "qa-triage-parent",
                    "user_prompt": "Review the alert.",
                },
            }
        ],
    )

    references = workflow_references(DSLInput.model_validate(workflow["definition"]))

    assert references.preset_slugs == {"qa-triage-parent"}
    assert references.versioned_preset_slugs == set()

    workflow["definition"]["actions"][0]["args"]["preset_version"] = 2
    references = workflow_references(DSLInput.model_validate(workflow["definition"]))

    assert references.preset_slugs == set()
    assert references.versioned_preset_slugs == {VersionedSlug("qa-triage-parent", 2)}


def test_skill_fixture_records_file_sha256s() -> None:
    files = _expanded_full_git_tree(include_schedules=False)
    version_spec = yaml.safe_load(
        files[f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml"]
    )

    recorded_hashes = {file["path"]: file["sha256"] for file in version_spec["files"]}
    for file_path, expected_hash in recorded_hashes.items():
        content = files[
            f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/files/{file_path}"
        ]
        assert hashlib.sha256(content.encode()).hexdigest() == expected_hash


def test_canonical_json_rejects_unsupported_objects() -> None:
    with pytest.raises(PydanticSerializationError):
        canonical_json_text(object())


@pytest.mark.anyio
async def test_full_expanded_fixture_parses_every_resource_type(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    snapshot, diagnostics = await workspace_sync_service.parse_files(
        _expanded_full_git_tree(include_schedules=False)
    )

    assert diagnostics == []
    assert len(snapshot.spec.workflows) == 3
    assert len(snapshot.spec.agent_presets) == 2
    assert len(snapshot.spec.skills) == 1
    assert len(snapshot.spec.tables) == 1
    assert len(snapshot.spec.case_tags) == 1
    assert len(snapshot.spec.case_dropdowns) == 1
    assert len(snapshot.spec.case_durations) == 1
    assert len(snapshot.spec.case_fields) == 1
    assert len(snapshot.spec.variables) == 1
    assert len(snapshot.spec.secret_metadata) == 1


@pytest.mark.anyio
async def test_pull_dry_run_reports_per_resource_counts(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="b" * 40,
        tree_sha="tree-sha",
        files=files,
    )
    workspace_sync_service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )
    workspace_sync_service.project_workspace = AsyncMock(
        return_value=WorkspaceProjection(
            manifest=WorkspaceManifest(),
            spec=WorkspaceSpec(),
            files={MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())},
        )
    )
    workspace_sync_service._validate_workflow_import = AsyncMock(return_value=[])

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        result = await workspace_sync_service.pull(
            options=PullOptions(commit_sha="b" * 40, dry_run=True)
        )

    assert result.success is True
    resource_counts = result.resource_counts
    assert resource_counts is not None
    assert resource_counts["workflow"].found == 2
    assert resource_counts["agent_preset"].found == 2
    assert resource_counts["skill"].found == 1
    assert resource_counts["table"].found == 1
    assert resource_counts["case_tag"].found == 1
    assert resource_counts["variable"].found == 1
    assert resource_counts["secret_metadata"].found == 1


@pytest.mark.anyio
async def test_pull_dry_run_validation_does_not_normalize_existing_workflow(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    workflow = Workflow(
        workspace_id=svc_role.workspace_id,
        title="QA preview workflow",
        description="Existing workflow missing system resource rows",
        alias="qa-preview",
    )
    session.add(workflow)
    await session.flush()
    snapshot = WorkspaceRemoteSnapshot(
        commit_sha="d" * 40,
        files={},
        spec=WorkspaceSpec(
            workflows={
                "qa-preview": WorkflowResourceSpec.model_validate(
                    _workflow_spec(
                        source_id="qa-preview",
                        title="QA preview workflow",
                        alias="qa-preview",
                        folder_path="QA/Preview",
                        actions=[
                            {
                                "ref": "shape",
                                "action": "core.transform.reshape",
                                "args": {"value": "${{ TRIGGER.value }}"},
                            }
                        ],
                    )
                )
            }
        ),
    )

    diagnostics = await WorkspaceSyncService(
        session=session,
        role=svc_role,
    )._validate_workflow_import(snapshot)

    assert diagnostics == []
    assert (
        await session.scalar(select(Webhook).where(Webhook.workflow_id == workflow.id))
    ) is None
    assert (
        await session.scalar(
            select(CaseTrigger).where(CaseTrigger.workflow_id == workflow.id)
        )
    ) is None


@pytest.mark.anyio
async def test_missing_child_workflow_alias_is_dependency_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    del files[f"{WORKFLOW_ROOT}/qa-child/definition.yml"]

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "dependency" and "qa-child" in diagnostic.message
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_duplicate_workflow_alias_is_dependency_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    root_path = f"{WORKFLOW_ROOT}/qa-root/definition.yml"
    root_workflow = yaml.safe_load(files[root_path])
    root_workflow["alias"] = "qa-child"
    files[root_path] = _yaml(root_workflow)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "dependency"
        and "duplicate workflow alias" in diagnostic.message.lower()
        and "qa-child" in diagnostic.message
        and diagnostic.details.get("workflow_source_ids") == ["qa-child", "qa-root"]
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_duplicate_skill_slug_is_validation_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    skill_path = f"{SKILL_ROOT}/qa-enrichment-skill-copy/skill.yml"
    skill = yaml.safe_load(files[f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml"])
    skill.update(
        {
            "id": "qa-enrichment-skill-copy",
            "name": "QA enrichment skill copy",
            "current_version": None,
            "files": [],
        }
    )
    files[skill_path] = _yaml(skill)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.workflow_path == f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml"
        and diagnostic.error_type == "validation"
        and "duplicate skill slug" in diagnostic.message.lower()
        and diagnostic.details.get("identity") == ["qa-enrichment-skill"]
        and diagnostic.details.get("source_ids")
        == ["qa-enrichment-skill", "qa-enrichment-skill-copy"]
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_duplicate_variable_environment_name_is_validation_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    variable_path = f"{VARIABLE_ROOT}/default/qa_config_copy.yml"
    variable = yaml.safe_load(files[f"{VARIABLE_ROOT}/default/qa_config.yml"])
    variable["id"] = "default/qa_config_copy"
    files[variable_path] = _yaml(variable)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.workflow_path == f"{VARIABLE_ROOT}/default/qa_config.yml"
        and diagnostic.error_type == "validation"
        and "duplicate variable target" in diagnostic.message.lower()
        and diagnostic.details.get("identity") == ["default", "qa_config"]
        and diagnostic.details.get("source_ids")
        == ["default/qa_config", "default/qa_config_copy"]
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_missing_preset_slug_is_dependency_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    root_path = f"{WORKFLOW_ROOT}/qa-root/definition.yml"
    root_workflow = yaml.safe_load(files[root_path])
    root_workflow["definition"]["actions"][1]["args"]["preset_slug"] = "missing-preset"
    files[root_path] = _yaml(root_workflow)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "dependency" and "missing-preset" in diagnostic.message
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_missing_skill_binding_is_dependency_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    del files[f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml"]

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "dependency"
        and "qa-enrichment-skill" in diagnostic.message
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_missing_pinned_skill_version_is_dependency_diagnostic(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    del files[f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml"]

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "dependency"
        and "qa-enrichment-skill" in diagnostic.message
        and "@1" in diagnostic.message
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_secret_values_in_git_are_rejected(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    secret_path = f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml"
    secret_spec = yaml.safe_load(files[secret_path])
    secret_spec["values"] = {"API_KEY": "do-not-export", "BASE_URL": "do-not-export"}
    files[secret_path] = _yaml(secret_spec)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "validation"
        and "secret value" in diagnostic.message.lower()
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
@pytest.mark.parametrize("value_field", ["value", "values"])
async def test_variable_values_in_git_are_rejected(
    workspace_sync_service: WorkspaceSyncService,
    value_field: str,
) -> None:
    files = _expanded_selected_git_tree()
    variable_path = f"{VARIABLE_ROOT}/default/qa_config.yml"
    variable_spec = yaml.safe_load(files[variable_path])
    variable_spec[value_field] = {"mode": "do-not-export"}
    files[variable_path] = _yaml(variable_spec)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "validation"
        and "variable value" in diagnostic.message.lower()
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_cyclic_preset_subagent_references_are_dependency_diagnostics(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    child_path = f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml"
    child_preset = yaml.safe_load(files[child_path])
    child_preset["subagents"] = [{"slug": "qa-triage-parent"}]
    files[child_path] = _yaml(child_preset)

    _, diagnostics = await workspace_sync_service.parse_files(files)

    assert any(
        diagnostic.error_type == "dependency" and "cyclic" in diagnostic.message.lower()
        for diagnostic in diagnostics
    )


@pytest.mark.anyio
async def test_import_selected_fixture_reconciles_supported_non_workflow_resources(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_selected_git_tree(),
        commit_sha="c" * 40,
    )

    assert diagnostics == []
    imported_resources = await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    imported_counts: dict[str, int] = {}
    for imported in imported_resources:
        imported_counts[imported.resource_type.value] = (
            imported_counts.get(imported.resource_type.value, 0) + 1
        )

    assert imported_counts["agent_preset"] == 2
    assert imported_counts["skill"] == 1
    assert imported_counts["table"] == 1
    assert imported_counts["case_tag"] == 1
    assert imported_counts["variable"] == 1
    assert imported_counts["secret_metadata"] == 1

    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    parent_preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert parent_preset is not None
    assert parent_preset.agents["enabled"] is True
    assert parent_preset.agents["subagents"][0]["preset"] == "qa-evidence-child"
    assert parent_preset.base_url == "https://models.example.test/v1"
    assert parent_preset.output_type == {"type": "json_schema", "name": "qa_triage"}
    assert parent_preset.namespaces == ["tools.qa_enrichment"]
    assert parent_preset.mcp_integrations == ["qa-mcp"]
    assert parent_preset.retries == 4
    assert parent_preset.enable_thinking is False
    assert parent_preset.enable_internet_access is True
    first_parent_version_id = parent_preset.current_version_id

    second_imported_resources = await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    second_imported_counts: dict[str, int] = {}
    for imported in second_imported_resources:
        second_imported_counts[imported.resource_type.value] = (
            second_imported_counts.get(imported.resource_type.value, 0) + 1
        )

    assert second_imported_counts == imported_counts
    await session.refresh(parent_preset)
    assert parent_preset.current_version_id == first_parent_version_id
    preset_versions = list(
        (
            await session.scalars(
                select(AgentPresetVersion).where(
                    AgentPresetVersion.workspace_id == workspace_id,
                    AgentPresetVersion.preset_id == parent_preset.id,
                )
            )
        ).all()
    )
    assert len(preset_versions) == 1

    skill = await session.scalar(
        select(Skill).where(
            Skill.workspace_id == workspace_id,
            Skill.name == "qa-enrichment-skill",
        )
    )
    assert skill is not None
    skill_version = await session.scalar(
        select(SkillVersion).where(
            SkillVersion.workspace_id == workspace_id,
            SkillVersion.skill_id == skill.id,
            SkillVersion.version == 1,
        )
    )
    assert skill_version is not None
    assert skill_version.name == "QA enrichment skill"
    draft_paths = list(
        (
            await session.scalars(
                select(SkillDraftFile.path)
                .where(
                    SkillDraftFile.workspace_id == workspace_id,
                    SkillDraftFile.skill_id == skill.id,
                )
                .order_by(SkillDraftFile.path.asc())
            )
        ).all()
    )
    assert set(draft_paths) == {"SKILL.md", "enrich.py"}
    assert await session.scalar(
        select(Table).where(
            Table.workspace_id == workspace_id,
            Table.name == "qa_indicators",
        )
    )
    assert await session.scalar(
        select(CaseTag).where(
            CaseTag.workspace_id == workspace_id,
            CaseTag.ref == "qa-alert",
        )
    )
    variable = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == workspace_id,
            WorkspaceVariable.name == "qa_config",
            WorkspaceVariable.environment == "default",
        )
    )
    assert variable is not None
    assert variable.description == "QA config variable"
    assert variable.tags == {"qa-sync": ""}
    secret = await session.scalar(
        select(Secret).where(
            Secret.workspace_id == workspace_id,
            Secret.name == "qa_threatintel",
            Secret.environment == "default",
        )
    )
    assert secret is not None
    assert secret.description == "QA threat intel credentials"


@pytest.mark.anyio
async def test_project_workspace_exports_supported_non_workflow_resources(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_full_git_tree(include_schedules=False),
        commit_sha="d" * 40,
    )

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    dropdown = await session.scalar(
        select(CaseDropdownDefinition).where(
            CaseDropdownDefinition.workspace_id == svc_role.workspace_id,
            CaseDropdownDefinition.ref == "qa_resolution_reason",
        )
    )
    assert dropdown is not None
    assert dropdown.is_ordered is True
    assert dropdown.icon_name == "ListChecks"
    assert dropdown.position == 4
    assert dropdown.required_on_closure is True

    projection = await service.project_workspace()
    files = projection.files
    expected_non_workflow_paths = {
        path
        for path in _expected_full_paths()
        if not path.startswith(f"{WORKFLOW_ROOT}/")
    }

    assert expected_non_workflow_paths <= set(files)
    assert not any(path.startswith(f"{WORKFLOW_ROOT}/") for path in files)
    _assert_secret_metadata_has_no_values(files)
    _assert_variable_exports_have_no_values(files)

    assert f"{TABLE_ROOT}/qa_indicators/rows.jsonl" not in files
    table_spec = yaml.safe_load(files[f"{TABLE_ROOT}/qa_indicators/table.yml"])
    assert "rows_path" not in table_spec
    assert "rows" not in table_spec
    skill_version = yaml.safe_load(
        files[f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml"]
    )
    assert {file["path"] for file in skill_version["files"]} == {
        "SKILL.md",
        "enrich.py",
    }
    parent_preset = yaml.safe_load(
        files[f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml"]
    )
    assert parent_preset["folder_path"] == "/QA/Agents/"
    assert parent_preset["tags"] == ["qa-sync"]
    assert "instructions" not in parent_preset
    parent_version = yaml.safe_load(
        files[f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml"]
    )
    assert parent_version["instructions"] == (
        "Use the enrichment skill and escalate high severity."
    )


@pytest.mark.anyio
async def test_agent_preset_projection_excludes_soft_deleted_presets(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Workspace export excludes soft-deleted presets and their versions."""
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_full_git_tree(include_schedules=False),
        commit_sha="d" * 40,
    )
    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    parent_preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert parent_preset is not None
    parent_preset.deleted_at = datetime.now(UTC)
    session.add(parent_preset)
    await session.flush()

    projection = await service.project_workspace()

    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml" not in projection.files
    assert not any(
        path.startswith(f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/")
        for path in projection.files
    )


@pytest.mark.anyio
async def test_skill_projection_excludes_soft_deleted_preset_version_pins(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Skill version snapshots pinned only by soft-deleted presets are not exported."""
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _versioned_agent_skill_git_tree(),
        commit_sha="d" * 40,
    )
    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    # agent-x is the only preset pinning skill-a v1 (head is v2, agent-y pins v2).
    pinning_preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "agent-x",
        )
    )
    assert pinning_preset is not None
    pinning_preset.deleted_at = datetime.now(UTC)
    session.add(pinning_preset)
    await session.flush()

    projection = await service.project_workspace()

    assert f"{SKILL_ROOT}/skill-a/versions/1/version.yml" not in projection.files
    assert f"{SKILL_ROOT}/skill-a/versions/2/version.yml" in projection.files


@pytest.mark.anyio
async def test_agent_preset_import_ignores_soft_deleted_source_id_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Workspace import does not adopt a mapped soft-deleted preset row."""
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_selected_git_tree(),
        commit_sha="d" * 40,
    )
    assert diagnostics == []
    soft_deleted_preset = AgentPreset(
        workspace_id=workspace_id,
        slug="qa-triage-parent",
        name="Soft-deleted QA triage",
        model_name="gpt-4o-mini",
        model_provider="openai",
        deleted_at=datetime.now(UTC),
    )
    session.add(soft_deleted_preset)
    await session.flush()
    session.add(
        WorkspaceSyncResourceMapping(
            workspace_id=workspace_id,
            provider=VcsProvider.GITHUB.value,
            resource_type=SyncResourceType.AGENT_PRESET.value,
            source_id="qa-triage-parent",
            source_path=f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml",
            local_id=soft_deleted_preset.id,
        )
    )
    await session.flush()

    imported_resources = await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    imported_parent = next(
        resource
        for resource in imported_resources
        if resource.resource_type is SyncResourceType.AGENT_PRESET
        and resource.source_id == "qa-triage-parent"
    )
    assert imported_parent.local_id != soft_deleted_preset.id
    active_parent = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == workspace_id,
            AgentPreset.slug == "qa-triage-parent",
            AgentPreset.deleted_at.is_(None),
        )
    )
    assert active_parent is not None
    assert imported_parent.local_id == active_parent.id
    await session.refresh(soft_deleted_preset)
    assert soft_deleted_preset.deleted_at is not None


@pytest.mark.anyio
async def test_agent_preset_import_resolves_subagent_to_active_preset(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Subagent slug resolution must skip a soft-deleted preset holding the slug."""
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_selected_git_tree(),
        commit_sha="d" * 40,
    )
    assert diagnostics == []
    # A soft-deleted preset keeps its slug and a resolvable current version, so an
    # unfiltered slug lookup could bind it as the parent's subagent target.
    soft_deleted_child = AgentPreset(
        workspace_id=workspace_id,
        slug="qa-evidence-child",
        name="Soft-deleted QA evidence child",
        model_name="gpt-4o-mini",
        model_provider="openai",
        deleted_at=datetime.now(UTC),
    )
    session.add(soft_deleted_child)
    await session.flush()
    soft_deleted_version = AgentPresetVersion(
        workspace_id=workspace_id,
        preset_id=soft_deleted_child.id,
        version=1,
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    session.add(soft_deleted_version)
    await session.flush()
    soft_deleted_child.current_version_id = soft_deleted_version.id
    session.add(soft_deleted_child)
    await session.flush()

    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    active_child = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == workspace_id,
            AgentPreset.slug == "qa-evidence-child",
            AgentPreset.deleted_at.is_(None),
        )
    )
    assert active_child is not None
    assert active_child.id != soft_deleted_child.id
    parent_preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == workspace_id,
            AgentPreset.slug == "qa-triage-parent",
            AgentPreset.deleted_at.is_(None),
        )
    )
    assert parent_preset is not None
    assert parent_preset.agents["enabled"] is True
    assert [
        subagent["preset_id"] for subagent in parent_preset.agents["subagents"]
    ] == [str(active_child.id)]


@pytest.mark.anyio
async def test_agent_preset_subagent_target_skips_soft_deleted_preset(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A subagent slug held only by a soft-deleted preset must not resolve."""
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    soft_deleted_child = AgentPreset(
        workspace_id=workspace_id,
        slug="qa-soft-deleted-only-child",
        name="Soft-deleted QA child",
        model_name="gpt-4o-mini",
        model_provider="openai",
        deleted_at=datetime.now(UTC),
    )
    session.add(soft_deleted_child)
    await session.flush()
    soft_deleted_version = AgentPresetVersion(
        workspace_id=workspace_id,
        preset_id=soft_deleted_child.id,
        version=1,
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    session.add(soft_deleted_version)
    await session.flush()
    soft_deleted_child.current_version_id = soft_deleted_version.id
    session.add(soft_deleted_child)
    await session.flush()
    importer = WorkspaceResourceImportService(session=session, role=svc_role)

    target = await AGENT_PRESET_RESOURCE_ADAPTER._resolved_subagent_target(
        importer,
        AgentPresetSubagentRef(slug="qa-soft-deleted-only-child"),
    )

    assert target is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "repo_url", "git_url"),
    [
        (
            VcsProvider.GITHUB,
            "git+ssh://git@github.com/TracecatHQ/git-sync-qa.git",
            GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa"),
        ),
        (
            VcsProvider.GITLAB,
            "git+ssh://git@gitlab.example.test/TracecatHQ/platform/git-sync-qa.git",
            GitUrl(
                host="gitlab.example.test",
                org="TracecatHQ/platform",
                repo="git-sync-qa",
            ),
        ),
    ],
)
async def test_source_export_target_pull_preserves_projected_workspace(
    session: AsyncSession,
    svc_role: Role,
    provider: VcsProvider,
    repo_url: str,
    git_url: GitUrl,
) -> None:
    """Round-trip a workspace through VCS and assert target parity.

    This is the black-box acceptance flow for workspace sync: a source workspace
    pushes canonical sync files to a shared repo, a target workspace pulls them,
    and both workspaces project back to the same canonical file set. The second
    push/pull cycle proves in-place updates preserve parity after renames,
    metadata edits, and an added resource.
    """
    assert svc_role.workspace_id is not None
    fake_vcs = FakeVcsServer()
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
        provider=provider,
    )
    target_role = await _create_workspace_role(
        session,
        source_role=svc_role,
        workspace_name="target-workspace",
        repo_url=repo_url,
        provider=provider,
    )
    source_service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        provider=provider,
        transport_factory=fake_vcs.transport_factory,
    )
    target_service = WorkspaceSyncService(
        session=session,
        role=target_role,
        provider=provider,
        transport_factory=fake_vcs.transport_factory,
    )
    seed_transport = fake_vcs.transport_factory(
        provider,
        session=session,
        role=svc_role,
    )
    seed_commit = await seed_transport.write_files(
        url=git_url,
        files=_expanded_full_git_tree(include_schedules=False),
        message="Seed source workspace",
        branch="seed/source",
        create_pr=False,
    )
    assert seed_commit.sha is not None

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        source_pull = await source_service.pull(
            options=PullOptions(commit_sha=seed_commit.sha),
        )
        assert source_pull.success is True
        source_table = await session.scalar(
            select(Table).where(
                Table.workspace_id == svc_role.workspace_id,
                Table.name == "qa_indicators",
            )
        )
        assert source_table is not None
        table_source_ids = await TABLE_RESOURCE_ADAPTER.source_ids_by_local_id(
            source_service
        )
        assert table_source_ids.get(source_table.id) == "qa_indicators"

        first_export = await source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Push source workspace",
                branch="sync/source-to-target",
                create_pr=False,
            )
        )
        assert first_export.commit.status is PushStatus.COMMITTED
        assert first_export.commit.sha is not None

        first_target_pull = await target_service.pull(
            options=PullOptions(commit_sha=first_export.commit.sha),
        )
        assert first_target_pull.success is True
        await _assert_projected_workspaces_match(
            source_service,
            target_service,
        )

        # Exercise a representative update batch before the second push/pull:
        # mapped-resource renames, metadata edits, and one new resource.
        await _mutate_source_workspace_for_roundtrip_update(session, role=svc_role)
        renamed_source_table = await session.scalar(
            select(Table).where(
                Table.workspace_id == svc_role.workspace_id,
                Table.name == "qa_indicators_roundtrip",
            )
        )
        assert renamed_source_table is not None
        assert renamed_source_table.id == source_table.id
        renamed_table_source_ids = await TABLE_RESOURCE_ADAPTER.source_ids_by_local_id(
            source_service
        )
        assert renamed_table_source_ids.get(renamed_source_table.id) == "qa_indicators"
        second_export = await source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Push source workspace update",
                branch="sync/source-to-target",
                create_pr=False,
            )
        )
        assert second_export.commit.status is PushStatus.COMMITTED
        assert second_export.commit.sha is not None
        source_projection = await source_service.project_workspace(
            create_missing_mappings=False
        )
        assert f"{TABLE_ROOT}/qa_indicators/table.yml" in source_projection.files
        assert (
            f"{TABLE_ROOT}/qa_indicators_roundtrip/table.yml"
            not in source_projection.files
        )
        # Confirm the fake VCS commit stores exactly what source projection emits.
        assert (
            fake_vcs.repo_files(git_url, ref=second_export.commit.sha)
            == source_projection.files
        )

        second_target_pull = await target_service.pull(
            options=PullOptions(commit_sha=second_export.commit.sha),
        )

    assert second_target_pull.success is True
    await _assert_projected_workspaces_match(
        source_service,
        target_service,
    )


@pytest.mark.anyio
async def test_selected_export_by_source_id_targets_mapped_resource_after_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Export one selected resource by Git source id after a local rename.

    UI/API callers can target a resource by ``source_id`` rather than a local DB
    UUID. That path must resolve through ``WorkspaceSyncResourceMapping`` so a
    mutable local ref/name change does not make us export the wrong resource. The
    source workspace starts with two case tags: one mapped tag that gets renamed
    locally, and one unmapped neighbor. Exporting by the mapped tag's original
    source id should push only that tag, then the destination should import only
    that intended resource.
    """
    assert svc_role.workspace_id is not None
    # ======================================================================
    # ARRANGE: source imports a mapped resource from Git
    # ======================================================================
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-source-id-qa.git"
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-source-id-qa")
    fake_vcs = FakeVcsServer()
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    target_role = await _create_workspace_role(
        session,
        source_role=svc_role,
        workspace_name="target-source-id-workspace",
        repo_url=repo_url,
    )
    source_service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=fake_vcs.transport_factory,
    )
    target_service = WorkspaceSyncService(
        session=session,
        role=target_role,
        transport_factory=fake_vcs.transport_factory,
    )
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=session,
        role=svc_role,
    )
    seed_commit = await seed_transport.write_files(
        url=git_url,
        files=_expanded_full_git_tree(include_schedules=False),
        message="Seed source workspace for source-id selection",
        branch="seed/source",
        create_pr=False,
    )
    assert seed_commit.sha is not None

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        source_pull = await source_service.pull(
            options=PullOptions(commit_sha=seed_commit.sha)
        )
        assert source_pull.success is True

        # ==================================================================
        # SOURCE MUTATION: rename the mapped resource and add a neighbor
        # ==================================================================
        # Rename the mapped tag locally so its ref no longer matches its Git
        # source id, then add an unmapped neighbor. A correct source-id export
        # must follow the mapping (not the mutable ref) and must leave the
        # neighbor behind.
        selected_tag = await session.scalar(
            select(CaseTag).where(
                CaseTag.workspace_id == svc_role.workspace_id,
                CaseTag.ref == "qa-alert",
            )
        )
        assert selected_tag is not None
        selected_tag_id = selected_tag.id
        selected_tag.ref = "qa-alert-renamed-locally"
        selected_tag.name = "QA alert selected by source id"
        neighbor_tag = CaseTag(
            workspace_id=svc_role.workspace_id,
            ref="qa-neighbor",
            name="QA neighbor should stay local",
            color="#303030",
        )
        session.add_all([selected_tag, neighbor_tag])
        await session.flush()
        # Even after the rename, the mapping still resolves the original source
        # id back to the same local row.
        await _assert_mapping_targets(
            session,
            role=svc_role,
            resource_type=SyncResourceType.CASE_TAG,
            source_id="qa-alert",
            local_id=selected_tag_id,
        )

        # ==================================================================
        # EXPORT: source_id selection resolves to the renamed mapped row
        # ==================================================================
        # Select by the original source id; this must resolve through the
        # mapping to the locally-renamed row, not match on the mutable ref/name.
        selected_export = await source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Push selected case tag by source id",
                branch="sync/source-id-selection",
                create_pr=False,
                resources=[
                    ResourceRef(
                        resource_type=SyncResourceType.CASE_TAG,
                        source_id="qa-alert",
                    )
                ],
            )
        )
        assert selected_export.commit.status is PushStatus.COMMITTED
        assert selected_export.commit.sha is not None
        selected_remote_files = fake_vcs.repo_files(
            git_url,
            ref=selected_export.commit.sha,
        )
        # Only the selected tag (plus the synthesised manifest) is pushed; the
        # unmapped neighbor is never written.
        assert set(selected_remote_files) == {
            MANIFEST_FILENAME,
            f"{CASE_TAG_ROOT}/qa-alert.yml",
        }
        selected_spec = yaml.safe_load(
            selected_remote_files[f"{CASE_TAG_ROOT}/qa-alert.yml"]
        )
        # The exported file keeps the canonical source id while carrying the
        # local name edit, confirming the row (not the ref) was resolved.
        assert selected_spec["id"] == "qa-alert"
        assert selected_spec["name"] == "QA alert selected by source id"
        assert f"{CASE_TAG_ROOT}/qa-neighbor.yml" not in selected_remote_files

        # ==================================================================
        # PULL: destination materialises only the intended selected resource
        # ==================================================================
        # The destination upgrades by pulling the selection commit, importing
        # exactly the one resource that was pushed.
        target_pull = await target_service.pull(
            options=PullOptions(commit_sha=selected_export.commit.sha)
        )

    assert target_pull.success is True
    target_workspace_id = target_role.workspace_id
    assert target_workspace_id is not None
    # The destination holds exactly the selected tag — keyed by the canonical
    # source id, carrying the local name edit — and nothing from the neighbor.
    target_tags = list(
        (
            await session.scalars(
                select(CaseTag).where(CaseTag.workspace_id == target_workspace_id)
            )
        ).all()
    )
    assert len(target_tags) == 1
    target_tag = target_tags[0]
    assert target_tag.ref == "qa-alert"
    assert target_tag.name == "QA alert selected by source id"
    # ...and the destination records its own mapping back to the source id.
    await _assert_mapping_targets(
        session,
        role=target_role,
        resource_type=SyncResourceType.CASE_TAG,
        source_id="qa-alert",
        local_id=target_tag.id,
    )


@pytest.mark.anyio
async def test_agent_preset_only_export_lazily_includes_dependency_closure(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-preset-closure-qa.git"
    git_url = GitUrl(
        host="github.com",
        org="TracecatHQ",
        repo="git-sync-preset-closure-qa",
    )
    fake_vcs = FakeVcsServer()
    assert svc_role.workspace_id is not None
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=fake_vcs.transport_factory,
    )
    await _import_expanded_non_workflow_resources(
        session,
        role=svc_role,
        service=service,
    )
    parent_preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert parent_preset is not None
    assert parent_preset.current_version_id is not None
    parent_version = await session.scalar(
        select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == svc_role.workspace_id,
            AgentPresetVersion.id == parent_preset.current_version_id,
        )
    )
    assert parent_version is not None
    parent_version.instructions = (
        "Use ${{ VARS.qa_config }} and "
        "${{ SECRETS.qa_threatintel.BASE_URL }}. "
        "Mention qa_indicators as prose only."
    )
    session.add(parent_version)
    await session.flush()

    export = await service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Export preset closure",
            branch="sync/preset-closure",
            create_pr=False,
            resources=[ResourceRef(resource_type=SyncResourceType.AGENT_PRESET)],
        )
    )

    assert export.commit.status is PushStatus.COMMITTED
    assert export.commit.sha is not None
    files = fake_vcs.repo_files(git_url, ref=export.commit.sha)
    assert not any(path.startswith(f"{WORKFLOW_ROOT}/") for path in files)
    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml" in files
    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml" in files
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml" in files
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml" in files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml" in files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml" in files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/files/SKILL.md" in files
    assert not any(
        path.startswith(f"{SKILL_ROOT}/qa-enrichment-skill/files/") for path in files
    )
    assert f"{VARIABLE_ROOT}/default/qa_config.yml" in files
    assert f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml" in files
    assert f"{TABLE_ROOT}/qa_indicators/table.yml" not in files
    assert not any(path.startswith(f"{CASE_TAG_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_FIELD_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_DROPDOWN_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_DURATION_ROOT}/") for path in files)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "preview_request",
    [
        pytest.param(
            WorkspaceSyncExportPreviewRequest(
                resources=[ResourceRef(resource_type=SyncResourceType.AGENT_PRESET)]
            ),
            id="agent_preset_type_export",
        ),
        pytest.param(WorkspaceSyncExportPreviewRequest(), id="full_export"),
    ],
)
async def test_export_merges_seen_subagent_versions(
    preview_request: WorkspaceSyncExportPreviewRequest,
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _versioned_subagent_git_tree(),
        commit_sha="2" * 40,
    )
    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    preview = await service.preview_export_workspace(preview_request)

    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml" in preview.files
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/2.yml" in preview.files


@pytest.mark.anyio
async def test_round_trip_preserves_presets_pinning_different_skill_versions(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Round-tripping presets that pin distinct skill versions keeps the pins.

    The Git tree carries one skill (``skill-a``) with two published versions and
    two presets that bind different versions of it: ``agent-x`` pins ``skill-a``
    v1 while ``agent-y`` pins v2. Importing, exporting, then pulling into a fresh
    workspace must reproduce both skill versions and both per-preset pins exactly
    rather than collapsing them onto a single (e.g. latest) version.
    """
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-versioned-agent-qa.git"
    git_url = GitUrl(
        host="github.com",
        org="TracecatHQ",
        repo="git-sync-versioned-agent-qa",
    )
    fake_vcs = FakeVcsServer()
    assert svc_role.workspace_id is not None
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    # A second workspace, sharing the same repo, receives the pull leg of the
    # round trip so import and pull cannot accidentally read the same rows.
    target_role = await _create_workspace_role(
        session,
        source_role=svc_role,
        workspace_name="versioned-agent-target",
        repo_url=repo_url,
    )
    source_service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=fake_vcs.transport_factory,
    )
    target_service = WorkspaceSyncService(
        session=session,
        role=target_role,
        transport_factory=fake_vcs.transport_factory,
    )

    # Seed the source workspace by parsing the fixture tree and importing it; the
    # tree is well-formed, so parsing must not surface any diagnostics.
    source_snapshot, diagnostics = await source_service.parse_files(
        _versioned_agent_skill_git_tree(),
        commit_sha="1" * 40,
    )
    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(source_snapshot.spec)

    # Export the source workspace back to Git: this is the leg that must emit the
    # pinned version snapshots, not just the current head of each skill.
    export = await source_service.export_workspace(
        WorkspaceSyncExportRequest(
            message="Export versioned agent skill bindings",
            branch="sync/versioned-agent-bindings",
            create_pr=False,
        )
    )

    assert export.commit.status is PushStatus.COMMITTED
    assert export.commit.sha is not None
    # Both pinned skill version snapshots must travel with the export so the pin
    # targets still exist when the commit is pulled elsewhere.
    exported_files = fake_vcs.repo_files(git_url, ref=export.commit.sha)
    assert f"{SKILL_ROOT}/skill-a/versions/1/version.yml" in exported_files
    assert f"{SKILL_ROOT}/skill-a/versions/2/version.yml" in exported_files

    # Pull the exported commit into the independent target workspace.
    pull = await target_service.pull(options=PullOptions(commit_sha=export.commit.sha))

    assert pull.success is True
    target_workspace_id = target_role.workspace_id
    assert target_workspace_id is not None
    skill = await session.scalar(
        select(Skill).where(
            Skill.workspace_id == target_workspace_id,
            Skill.name == "skill-a",
        )
    )
    assert skill is not None
    # Both versions land in the target with their original names intact.
    skill_versions = {
        version.version: version.name
        for version in (
            await session.scalars(
                select(SkillVersion)
                .where(
                    SkillVersion.workspace_id == target_workspace_id,
                    SkillVersion.skill_id == skill.id,
                )
                .order_by(SkillVersion.version.asc())
            )
        ).all()
    }
    assert skill_versions == {1: "Skill A v1", 2: "Skill A v2"}
    # Head bindings (``AgentPresetSkill``) wire each preset's live config to the
    # skill version it pinned: agent-x -> v1, agent-y -> v2.
    binding_rows = await session.execute(
        select(AgentPreset.slug, SkillVersion.version)
        .select_from(AgentPreset)
        .join(
            AgentPresetSkill,
            AgentPresetSkill.preset_id == AgentPreset.id,
        )
        .join(
            SkillVersion,
            AgentPresetSkill.skill_version_id == SkillVersion.id,
        )
        .where(AgentPreset.workspace_id == target_workspace_id)
        .order_by(AgentPreset.slug.asc())
    )
    bindings = dict(binding_rows.tuples().all())
    assert bindings == {"agent-x": 1, "agent-y": 2}
    # The immutable version snapshot of each preset (``AgentPresetVersionSkill``
    # under the preset's current version) must record the same pins, proving the
    # versioned binding table round-trips too, not only the mutable head.
    version_binding_rows = await session.execute(
        select(AgentPreset.slug, SkillVersion.version)
        .select_from(AgentPreset)
        .join(
            AgentPresetVersionSkill,
            AgentPresetVersionSkill.preset_version_id == AgentPreset.current_version_id,
        )
        .join(
            SkillVersion,
            AgentPresetVersionSkill.skill_version_id == SkillVersion.id,
        )
        .where(AgentPreset.workspace_id == target_workspace_id)
        .order_by(AgentPreset.slug.asc())
    )
    version_bindings = dict(version_binding_rows.tuples().all())
    assert version_bindings == {"agent-x": 1, "agent-y": 2}


@pytest.mark.anyio
async def test_full_workspace_export_includes_workflow_pinned_version_closure(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A full export follows pinned versions transitively from workflow to skill.

    The seeded workflow calls ``ai.preset_agent`` pinned to ``agent-x`` v1, while
    that preset's head is v2; v1 pins ``skill-a`` v1 and v2 pins ``skill-a`` v2.
    A whole-workspace export must walk this closure and emit both preset version
    snapshots and both skill version snapshots, so the workflow's pinned-but-
    non-head dependency (and the skill version it transitively pins) is not
    dropped in favor of only the current head.
    """
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-workflow-pin-qa.git"
    git_url = GitUrl(
        host="github.com",
        org="TracecatHQ",
        repo="git-sync-workflow-pin-qa",
    )
    fake_vcs = FakeVcsServer()
    assert svc_role.workspace_id is not None
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    source_service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=fake_vcs.transport_factory,
    )
    # Seed the remote with the fixture tree directly, then pull it into the
    # workspace so the export below has live local rows to project from.
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=session,
        role=svc_role,
    )
    seed_commit = await seed_transport.write_files(
        url=git_url,
        files=_workflow_pinned_agent_version_git_tree(),
        message="Seed workflow pinned agent version",
        branch="seed/workflow-pinned-agent",
        create_pr=False,
    )
    assert seed_commit.sha is not None

    # Stub the registry lock so pulling the workflow does not require resolving
    # real action bindings; this test only exercises the export closure.
    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        source_pull = await source_service.pull(
            options=PullOptions(commit_sha=seed_commit.sha)
        )
        assert source_pull.success is True

        # Export the entire workspace (no resource filter) to capture the full
        # transitive closure of the workflow's pinned references.
        export = await source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Export workflow pinned agent version",
                branch="sync/workflow-pinned-agent",
                create_pr=False,
            )
        )

    assert export.commit.status is PushStatus.COMMITTED
    assert export.commit.sha is not None
    exported_files = fake_vcs.repo_files(git_url, ref=export.commit.sha)
    # The workflow itself, plus both preset versions (v1 is pinned by the
    # workflow, v2 is the head) and both skill versions they pin transitively.
    assert f"{WORKFLOW_ROOT}/workflow-pins-agent/definition.yml" in exported_files
    assert f"{AGENT_PRESET_ROOT}/agent-x/versions/1.yml" in exported_files
    assert f"{AGENT_PRESET_ROOT}/agent-x/versions/2.yml" in exported_files
    assert f"{SKILL_ROOT}/skill-a/versions/1/version.yml" in exported_files
    assert f"{SKILL_ROOT}/skill-a/versions/2/version.yml" in exported_files


@pytest.mark.anyio
async def test_single_workflow_export_includes_dependency_closure(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-workflow-publish-qa.git"
    git_url = GitUrl(
        host="github.com",
        org="TracecatHQ",
        repo="git-sync-workflow-publish-qa",
    )
    fake_vcs = FakeVcsServer()
    assert svc_role.workspace_id is not None
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=fake_vcs.transport_factory,
    )
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=session,
        role=svc_role,
    )
    seed_commit = await seed_transport.write_files(
        url=git_url,
        files=_expanded_selected_git_tree(),
        message="Seed workflow publish dependencies",
        branch="seed/workflow-publish-dependencies",
        create_pr=False,
    )
    assert seed_commit.sha is not None

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        pull = await service.pull(options=PullOptions(commit_sha=seed_commit.sha))
        assert pull.success is True

        root_workflow = await session.scalar(
            select(Workflow).where(
                Workflow.workspace_id == svc_role.workspace_id,
                Workflow.alias == "qa-root",
            )
        )
        assert root_workflow is not None
        root_spec = yaml.safe_load(
            _expanded_selected_git_tree()[f"{WORKFLOW_ROOT}/qa-root/definition.yml"]
        )

        export = await service.export_workflow(
            workflow=root_workflow,
            dsl=DSLInput.model_validate(root_spec["definition"]),
            params=WorkspaceSyncExportRequest(
                message="Publish root workflow",
                branch="sync/workflow-publish-dependencies",
                create_pr=False,
            ),
        )

    assert export.commit.status is PushStatus.COMMITTED
    assert export.commit.sha is not None
    exported_files = fake_vcs.repo_files(git_url, ref=export.commit.sha)
    assert f"{WORKFLOW_ROOT}/qa-root/definition.yml" in exported_files
    assert f"{WORKFLOW_ROOT}/qa-child/definition.yml" in exported_files
    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml" in exported_files
    assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml" in exported_files
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml" in exported_files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml" in exported_files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml" in exported_files
    assert f"{VARIABLE_ROOT}/default/qa_config.yml" in exported_files
    assert f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml" in exported_files
    assert f"{TABLE_ROOT}/qa_indicators/table.yml" in exported_files


@pytest.mark.anyio
async def test_legacy_workflow_only_repo_upgrades_to_expanded_workspace_sync(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Upgrade an existing workflow-only sync repo through the new workspace sync.

    Models an existing installation before the expanded resource format ships:
    both source and destination workspaces already track a remote repository that
    contains only legacy ``workflows/`` files and no ``tracecat.json`` manifest.
    After upgrade, the source workspace can create non-workflow resources, export
    the full workspace with the new manifest, and the destination can pull that
    upgraded commit without losing the existing workflows.
    """
    assert svc_role.workspace_id is not None
    # ======================================================================
    # ARRANGE: stand up a legacy (pre-upgrade) installation
    # ======================================================================
    # Two workspaces (source + target) share one remote repo. ``repo_url`` is the
    # user-facing config string; ``git_url`` is the parsed form the fake VCS keys
    # its in-memory repos by.
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-upgrade-qa.git"
    git_url = GitUrl(
        host="github.com",
        org="TracecatHQ",
        repo="git-sync-upgrade-qa",
    )
    fake_vcs = FakeVcsServer()
    # Point the source workspace at the shared repo, then clone its config onto a
    # second workspace so both sides sync against the same remote.
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    target_role = await _create_workspace_role(
        session,
        source_role=svc_role,
        workspace_name="target-upgrade-workspace",
        repo_url=repo_url,
    )
    # One sync service per workspace; both share the fake VCS transport so they
    # read/write the same in-memory repo instead of hitting a real GitHub.
    source_service = WorkspaceSyncService(
        session=session,
        role=svc_role,
        transport_factory=fake_vcs.transport_factory,
    )
    target_service = WorkspaceSyncService(
        session=session,
        role=target_role,
        transport_factory=fake_vcs.transport_factory,
    )
    # Seed the remote with the *pre-upgrade* repo layout directly (bypassing the
    # sync services) so the starting commit looks like a legacy installation.
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=session,
        role=svc_role,
    )
    legacy_commit = await seed_transport.write_files(
        url=git_url,
        files=_legacy_workflow_only_git_tree(),
        message="Seed legacy workflow sync repository",
        branch="main",
        create_pr=False,
    )
    assert legacy_commit.sha is not None
    # Sanity-check the seed actually models the legacy format: no manifest file
    # and every tracked path lives under the workflows/ root.
    legacy_remote_files = fake_vcs.repo_files(git_url, ref=legacy_commit.sha)
    assert MANIFEST_FILENAME not in legacy_remote_files
    assert all(path.startswith(f"{WORKFLOW_ROOT}/") for path in legacy_remote_files)

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        # ==================================================================
        # PRE-UPGRADE: both workspaces sync the legacy workflow-only remote
        # ==================================================================
        # Both workspaces represent existing users before upgrade: they can pull
        # the workflow-only remote and preserve those workflow identities locally.
        source_legacy_pull = await source_service.pull(
            options=PullOptions(commit_sha=legacy_commit.sha)
        )
        target_legacy_pull = await target_service.pull(
            options=PullOptions(commit_sha=legacy_commit.sha)
        )
        # A successful pull on a manifest-less commit proves the new sync code is
        # backward compatible with the legacy layout.
        assert source_legacy_pull.success is True
        assert target_legacy_pull.success is True

        # Projecting the freshly-pulled target back to files shows what the new
        # code canonically emits for a legacy workspace.
        target_legacy_projection = await target_service.project_workspace(
            create_missing_mappings=False
        )
        # The manifest is synthesised on projection even though the remote never
        # had one...
        assert MANIFEST_FILENAME in target_legacy_projection.files
        # ...but no expanded resources (e.g. agent presets) are invented out of
        # thin air: a legacy repo only carried workflows.
        assert not any(
            path.startswith(f"{AGENT_PRESET_ROOT}/")
            for path in target_legacy_projection.files
        )

        # ==================================================================
        # UPGRADE: source gains expanded resources and pushes the new format
        # ==================================================================
        # Simulate post-upgrade source-side edits. parse_files validates the new
        # expanded tree (no diagnostics == it parsed cleanly), then the import
        # service writes the non-workflow resources into the source DB. These rows
        # are local database state only; the remote is still legacy until
        # export_workspace writes the new manifest and resource dirs below.
        expanded_snapshot, expanded_diagnostics = await source_service.parse_files(
            _expanded_full_git_tree(include_schedules=False),
            commit_sha="e" * 40,
        )
        assert expanded_diagnostics == []
        await WorkspaceResourceImportService(
            session=session,
            role=svc_role,
        ).import_non_workflow_resources(expanded_snapshot.spec)

        # Export the now-expanded source workspace. This is the upgrade commit:
        # the first push that writes the manifest + resource directories on top of
        # the legacy workflow files.
        upgraded_export = await source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Upgrade workspace sync repository",
                branch="sync/workspace-upgrade",
                create_pr=False,
            )
        )
        # The export must land as a real commit (not a no-op/skip) for the target
        # to have something to pull.
        assert upgraded_export.commit.status is PushStatus.COMMITTED
        assert upgraded_export.commit.sha is not None
        # The upgrade commit must now contain the manifest plus a representative
        # resource from each new family (preset / table / case tag) — proof the
        # legacy repo was actually expanded, not just re-pushed.
        upgraded_remote_files = fake_vcs.repo_files(
            git_url,
            ref=upgraded_export.commit.sha,
        )
        assert MANIFEST_FILENAME in upgraded_remote_files
        assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml" in (
            upgraded_remote_files
        )
        assert f"{TABLE_ROOT}/qa_indicators/table.yml" in upgraded_remote_files
        assert f"{CASE_TAG_ROOT}/qa-alert.yml" in upgraded_remote_files

        # ==================================================================
        # POST-UPGRADE: target pulls the upgraded commit
        # ==================================================================
        # The destination upgrades by pulling the new commit, exactly as a real
        # installation would after the source side ships the expanded format.
        target_upgraded_pull = await target_service.pull(
            options=PullOptions(commit_sha=upgraded_export.commit.sha)
        )

    assert target_upgraded_pull.success is True
    # Parity check: after the upgrade round-trip, source and target project to an
    # identical file set — no workflows lost, no resources dropped or duplicated.
    await _assert_projected_workspaces_match(source_service, target_service)
    # And the target DB actually materialised the new resource rows, not just the
    # files — i.e. the pull imported the expanded format end to end.
    await _assert_workspace_has_expanded_resource_rows(session, target_role)


@pytest.mark.anyio
async def test_legacy_string_manifest_repo_upgrades_to_expanded_workspace_sync(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Upgrade a workflow-only repo that already has the old manifest shape.

    Some legacy sync repos wrote a top-level ``tracecat.json`` with
    ``{"version":"1"}`` even though the repo still only contained workflow YAML.
    This test covers that slightly different upgrade path end to end: both
    workspaces pull the old manifest, the source exports the expanded numeric
    manifest + resource directories, and the destination pulls the upgraded
    commit without losing the legacy workflows.
    """
    assert svc_role.workspace_id is not None
    # ======================================================================
    # ARRANGE: seed the historical string-version manifest layout
    # ======================================================================
    harness = await _legacy_upgrade_harness(
        session,
        source_role=svc_role,
        repo_name="git-sync-legacy-manifest-upgrade-qa",
        target_workspace_name="target-legacy-manifest-upgrade-workspace",
        legacy_files=_legacy_string_manifest_git_tree(),
    )
    legacy_remote_files = harness.fake_vcs.repo_files(
        harness.git_url,
        ref=harness.legacy_commit_sha,
    )
    # The starting point is still a legacy workflow-only repo, but with the
    # historical string-version manifest present.
    assert json.loads(legacy_remote_files[MANIFEST_FILENAME]) == {"version": "1"}
    assert all(
        path == MANIFEST_FILENAME or path.startswith(f"{WORKFLOW_ROOT}/")
        for path in legacy_remote_files
    )

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        # ==================================================================
        # PRE-UPGRADE: both workspaces accept the legacy string manifest
        # ==================================================================
        # A successful pull here proves the new parser tolerates the historical
        # ``{"version":"1"}`` string manifest, not merely a missing manifest.
        source_legacy_pull = await harness.source_service.pull(
            options=PullOptions(commit_sha=harness.legacy_commit_sha)
        )
        target_legacy_pull = await harness.target_service.pull(
            options=PullOptions(commit_sha=harness.legacy_commit_sha)
        )
        assert source_legacy_pull.success is True
        assert target_legacy_pull.success is True

        # ==================================================================
        # UPGRADE: source writes the current manifest and expanded resources
        # ==================================================================
        # Simulate post-upgrade source edits: write the expanded resources into
        # the source DB only. The remote stays legacy until the export below.
        await _import_expanded_non_workflow_resources(
            session,
            role=svc_role,
            service=harness.source_service,
        )
        # Export is the upgrade commit; it must land as a real commit (not a
        # no-op skip) for the target to have something to pull.
        upgraded_export = await harness.source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Upgrade legacy manifest workspace sync repository",
                branch="sync/legacy-manifest-upgrade",
                create_pr=False,
            )
        )
        assert upgraded_export.commit.status is PushStatus.COMMITTED
        assert upgraded_export.commit.sha is not None
        upgraded_remote_files = harness.fake_vcs.repo_files(
            harness.git_url,
            ref=upgraded_export.commit.sha,
        )
        # The upgrade commit rewrites the old string manifest into the current
        # numeric-version contract and adds representative new resource roots,
        # proving the legacy repo was expanded rather than just re-pushed.
        assert json.loads(upgraded_remote_files[MANIFEST_FILENAME])["version"] == 1
        assert f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml" in (
            upgraded_remote_files
        )
        assert f"{VARIABLE_ROOT}/default/qa_config.yml" in upgraded_remote_files

        # ==================================================================
        # POST-UPGRADE: destination pulls the current expanded format
        # ==================================================================
        # The destination upgrades by pulling the new commit, exactly as a real
        # installation would once the source side ships the expanded format.
        target_upgraded_pull = await harness.target_service.pull(
            options=PullOptions(commit_sha=upgraded_export.commit.sha)
        )

    assert target_upgraded_pull.success is True
    # Parity check: source and target project to an identical file set, so no
    # workflows were lost and no resources were dropped or duplicated.
    await _assert_projected_workspaces_match(
        harness.source_service,
        harness.target_service,
    )
    # And the target DB materialised the new resource rows, not just the files.
    await _assert_workspace_has_expanded_resource_rows(session, harness.target_role)


def test_new_workflow_file_is_readable_by_legacy_remote_model() -> None:
    """Rollback safety: new-format workflow files stay readable by old sync code.

    The reverse of the legacy-upgrade tests. Those prove the new sync code reads
    an old repo (upgrade). This proves the inverse: a deployment rolled back to
    *before* the expanded format can still parse what the new exporter wrote,
    using the pre-upgrade :class:`RemoteWorkflowDefinition` model, so a rollback
    after a push does not silently break workflow pulls.

    Scope: holds for the system-minted ``wf_<short>`` source id (what
    ``WorkspaceSyncService`` assigns on first export). The new ``type``/``version``
    wrapper is tolerated because the legacy model already declares ``type`` and
    ignores the unknown ``version``. Slug source ids (e.g. human-authored
    ``workflows/my-detection/``) are the documented boundary and are *not*
    legacy-readable; that is asserted explicitly below.
    """
    source_id = WorkflowUUID.new(uuid.uuid4()).short()
    spec = WorkflowResourceSpec.model_validate(
        _workflow_spec(
            source_id=source_id,
            title="qa-rollback-workflow",
            alias="qa-rollback",
            folder_path="QA/Rollback",
            actions=[
                {
                    "ref": "reshape",
                    "action": "core.transform.reshape",
                    "args": {"value": "${{ TRIGGER.value }}"},
                }
            ],
            webhook=True,
            case_trigger=True,
        )
    )

    raw = yaml.safe_load(serialize_workflow_spec(spec))
    # New-format markers the legacy model never declared a schema for.
    assert raw["type"] == "workflow"
    assert raw["version"] == 1

    # The actual rollback parse: the old model must accept the new-format file.
    legacy = RemoteWorkflowDefinition.model_validate(raw)
    assert legacy.id == source_id
    assert legacy.alias == "qa-rollback"
    assert legacy.folder_path == "QA/Rollback"
    assert legacy.definition.title == "qa-rollback-workflow"
    assert legacy.webhook is not None
    assert legacy.case_trigger is not None

    # The minted source id satisfies the legacy short-id contract...
    assert re.fullmatch(WF_ID_SHORT_PATTERN, source_id) is not None
    # ...but a slug source id is the rollback boundary: the legacy model rejects
    # it, so slug-keyed files would not import after a rollback.
    with pytest.raises(ValidationError):
        RemoteWorkflowDefinition.model_validate({**raw, "id": "qa-rollback"})


@pytest.mark.anyio
async def test_legacy_upgrade_reuses_target_local_resources_without_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Pull an upgrade into a target that already has matching local resources.

    Existing users may have created agent presets, case tags, variables, or
    other config resources manually before those resources became Git-backed.
    Those rows do not have ``WorkspaceSyncResourceMapping`` entries yet. The
    upgraded pull should claim the existing rows by their natural identifiers,
    update them to the Git-owned state, and then attach sync mappings instead of
    duplicating resources in the destination workspace.
    """
    assert svc_role.workspace_id is not None
    # ======================================================================
    # ARRANGE: both workspaces start from the legacy workflow-only repo
    # ======================================================================
    harness = await _legacy_upgrade_harness(
        session,
        source_role=svc_role,
        repo_name="git-sync-local-resource-upgrade-qa",
        target_workspace_name="target-local-resource-upgrade-workspace",
        legacy_files=_legacy_workflow_only_git_tree(),
    )

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        # Both workspaces start as legacy installations that can pull the
        # workflow-only remote and keep those workflow identities locally.
        source_legacy_pull = await harness.source_service.pull(
            options=PullOptions(commit_sha=harness.legacy_commit_sha)
        )
        target_legacy_pull = await harness.target_service.pull(
            options=PullOptions(commit_sha=harness.legacy_commit_sha)
        )
        assert source_legacy_pull.success is True
        assert target_legacy_pull.success is True

        # ==================================================================
        # TARGET PRE-STATE: matching local resources exist but are not mapped
        # ==================================================================
        # Seed the target with resources whose natural identifiers match what
        # the upgrade will carry, modelling a user who built these by hand
        # before they became Git-backed. The pull must claim these rows rather
        # than create duplicates.
        await _import_expanded_non_workflow_resources(
            session,
            role=harness.target_role,
            service=harness.target_service,
        )
        target_workspace_id = harness.target_role.workspace_id
        assert target_workspace_id is not None
        preexisting_preset = await session.scalar(
            select(AgentPreset).where(
                AgentPreset.workspace_id == target_workspace_id,
                AgentPreset.slug == "qa-triage-parent",
            )
        )
        preexisting_tag = await session.scalar(
            select(CaseTag).where(
                CaseTag.workspace_id == target_workspace_id,
                CaseTag.ref == "qa-alert",
            )
        )
        preexisting_variable = await session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == target_workspace_id,
                WorkspaceVariable.name == "qa_config",
                WorkspaceVariable.environment == "default",
            )
        )
        assert preexisting_preset is not None
        assert preexisting_tag is not None
        assert preexisting_variable is not None
        preexisting_preset_id = preexisting_preset.id
        preexisting_tag_id = preexisting_tag.id
        preexisting_variable_id = preexisting_variable.id

        # Make the target rows visibly local/stale so the pull has to update
        # them, not merely discover identical rows.
        preexisting_preset.name = "Local pre-upgrade triage parent"
        preexisting_preset.instructions = "Local-only instructions before Git sync"
        preexisting_tag.name = "Local pre-upgrade alert"
        preexisting_variable.description = "Local pre-upgrade variable"
        session.add_all([preexisting_preset, preexisting_tag, preexisting_variable])
        await session.flush()

        # These local rows are unmanaged: no sync mapping ties them to a Git
        # source id yet, which is exactly the state the upgrade has to resolve.
        assert (
            await _mapping_for(
                session,
                role=harness.target_role,
                resource_type=SyncResourceType.AGENT_PRESET,
                source_id="qa-triage-parent",
            )
        ) is None
        assert (
            await _mapping_for(
                session,
                role=harness.target_role,
                resource_type=SyncResourceType.CASE_TAG,
                source_id="qa-alert",
            )
        ) is None
        assert (
            await _mapping_for(
                session,
                role=harness.target_role,
                resource_type=SyncResourceType.VARIABLE,
                source_id="default/qa_config",
            )
        ) is None

        # ==================================================================
        # UPGRADE: source pushes the expanded Git-owned workspace
        # ==================================================================
        # The source carries the same resources (same natural ids) the target
        # already has locally, so the upgrade commit is what forces the target
        # to reconcile its unmapped rows against Git ownership.
        await _import_expanded_non_workflow_resources(
            session,
            role=svc_role,
            service=harness.source_service,
        )
        upgraded_export = await harness.source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Upgrade workspace with preexisting target resources",
                branch="sync/local-resource-upgrade",
                create_pr=False,
            )
        )
        assert upgraded_export.commit.status is PushStatus.COMMITTED
        assert upgraded_export.commit.sha is not None

        # ==================================================================
        # POST-UPGRADE: target pull claims and updates the existing local rows
        # ==================================================================
        target_upgraded_pull = await harness.target_service.pull(
            options=PullOptions(commit_sha=upgraded_export.commit.sha)
        )

    assert target_upgraded_pull.success is True
    await _assert_projected_workspaces_match(
        harness.source_service,
        harness.target_service,
    )
    preset_after = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == target_workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    tag_after = await session.scalar(
        select(CaseTag).where(
            CaseTag.workspace_id == target_workspace_id,
            CaseTag.ref == "qa-alert",
        )
    )
    variable_after = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == target_workspace_id,
            WorkspaceVariable.name == "qa_config",
            WorkspaceVariable.environment == "default",
        )
    )
    assert preset_after is not None
    assert tag_after is not None
    assert variable_after is not None
    # Identical primary keys prove the upgrade claimed the preexisting local
    # rows by their natural ids, and the Git-owned field values replaced the
    # stale local edits made above instead of being skipped.
    assert preset_after.id == preexisting_preset_id
    assert preset_after.name == "QA triage parent"
    assert preset_after.instructions == (
        "Use the enrichment skill and escalate high severity."
    )
    assert tag_after.id == preexisting_tag_id
    assert tag_after.name == "qa-alert"
    assert variable_after.id == preexisting_variable_id
    assert variable_after.description == "QA config variable"
    # Exactly one row per natural id: the claim path updated in place rather
    # than inserting a duplicate alongside the original.
    assert (
        len(
            (
                await session.scalars(
                    select(AgentPreset).where(
                        AgentPreset.workspace_id == target_workspace_id,
                        AgentPreset.slug == "qa-triage-parent",
                    )
                )
            ).all()
        )
        == 1
    )
    assert (
        len(
            (
                await session.scalars(
                    select(CaseTag).where(
                        CaseTag.workspace_id == target_workspace_id,
                        CaseTag.ref == "qa-alert",
                    )
                )
            ).all()
        )
        == 1
    )
    # Each resource now has a sync mapping pointing back at the claimed local
    # row, so subsequent pulls treat these as Git-managed instead of orphaned.
    await _assert_mapping_targets(
        session,
        role=harness.target_role,
        resource_type=SyncResourceType.AGENT_PRESET,
        source_id="qa-triage-parent",
        local_id=preexisting_preset_id,
    )
    await _assert_mapping_targets(
        session,
        role=harness.target_role,
        resource_type=SyncResourceType.CASE_TAG,
        source_id="qa-alert",
        local_id=preexisting_tag_id,
    )
    await _assert_mapping_targets(
        session,
        role=harness.target_role,
        resource_type=SyncResourceType.VARIABLE,
        source_id="default/qa_config",
        local_id=preexisting_variable_id,
    )


@pytest.mark.anyio
async def test_upgraded_pull_rolls_back_non_workflow_resources_on_import_failure(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Failed upgraded pulls must not leave partial expanded resources behind.

    The expanded import writes several resource families before workflows and
    mappings are finalized. This test forces a failure partway through that
    non-workflow adapter loop, after earlier adapters have had a chance to
    flush rows. The destination should report a transaction failure, keep its
    legacy workflows operational, and contain none of the partially imported
    variable/secret/case resources from the failed upgraded commit.
    """
    assert svc_role.workspace_id is not None
    # ======================================================================
    # ARRANGE: destination starts from a healthy legacy workflow-only pull
    # ======================================================================
    harness = await _legacy_upgrade_harness(
        session,
        source_role=svc_role,
        repo_name="git-sync-upgrade-rollback-qa",
        target_workspace_name="target-upgrade-rollback-workspace",
        legacy_files=_legacy_workflow_only_git_tree(),
    )

    with patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    ):
        # Both workspaces first come up as healthy legacy installations.
        source_legacy_pull = await harness.source_service.pull(
            options=PullOptions(commit_sha=harness.legacy_commit_sha)
        )
        target_legacy_pull = await harness.target_service.pull(
            options=PullOptions(commit_sha=harness.legacy_commit_sha)
        )
        assert source_legacy_pull.success is True
        assert target_legacy_pull.success is True

        # ==================================================================
        # UPGRADE: source publishes a valid expanded commit
        # ==================================================================
        # Publish a genuinely valid expanded commit so the later failure is
        # attributable to the forced fault below, not to malformed input.
        await _import_expanded_non_workflow_resources(
            session,
            role=svc_role,
            service=harness.source_service,
        )
        upgraded_export = await harness.source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Upgrade workspace before forced import failure",
                branch="sync/rollback-upgrade",
                create_pr=False,
            )
        )
        assert upgraded_export.commit.status is PushStatus.COMMITTED
        assert upgraded_export.commit.sha is not None

        # ==================================================================
        # FAILURE: the target fails after earlier non-workflow adapters flush
        # ==================================================================
        # Tables import after several other resource families, so failing the
        # table adapter aborts the pull only once earlier adapters have already
        # flushed rows — the worst case for leaving partial writes behind.
        forced_failure = RuntimeError("forced table import failure")
        with patch.object(
            TABLE_RESOURCE_ADAPTER,
            "import_specs",
            AsyncMock(side_effect=forced_failure),
        ):
            failed_pull = await harness.target_service.pull(
                options=PullOptions(commit_sha=upgraded_export.commit.sha)
            )

    # The expected rollback expires ORM instances in the shared test session.
    # Refresh the fixture workspace so pytest cleanup can read it without
    # triggering lazy IO outside SQLAlchemy's async greenlet context.
    await _refresh_workspace_for_fixture_cleanup(session, role=svc_role)

    # The pull reports a structured transaction failure whose diagnostic names
    # the underlying fault, rather than a partial success.
    assert failed_pull.success is False
    assert failed_pull.message == "Workspace import transaction failed"
    assert any(
        diagnostic.error_type == "transaction"
        and "forced table import failure" in diagnostic.message
        for diagnostic in failed_pull.diagnostics
    )
    # The legacy workflow state survives the failed upgrade, so the workspace is
    # still projectable and contains the same workflow aliases as before.
    target_projection = await harness.target_service.project_workspace(
        create_missing_mappings=False
    )
    assert f"{WORKFLOW_ROOT}/qa-root/definition.yml" in target_projection.files
    assert not any(
        path.startswith(f"{AGENT_PRESET_ROOT}/") for path in target_projection.files
    )
    # The rollback was atomic: none of the expanded rows or sync mappings from
    # the aborted commit survived, even the ones flushed before the fault.
    await _assert_workspace_has_no_expanded_resource_rows(
        session,
        role=harness.target_role,
    )
    assert (
        await _mapping_for(
            session,
            role=harness.target_role,
            resource_type=SyncResourceType.CASE_TAG,
            source_id="qa-alert",
        )
    ) is None
    assert (
        await _mapping_for(
            session,
            role=harness.target_role,
            resource_type=SyncResourceType.VARIABLE,
            source_id="default/qa_config",
        )
    ) is None


@pytest.mark.anyio
async def test_project_workspace_preserves_agent_preset_source_id_after_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_selected_git_tree(),
        commit_sha="e" * 40,
    )

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    await service.project_workspace()
    parent_preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert parent_preset is not None

    parent_preset.name = "QA triage parent 9000"
    parent_preset.slug = "qa-triage-parent-9000"
    session.add(parent_preset)
    await session.flush()

    projection = await service.project_workspace(create_missing_mappings=False)
    old_path = f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml"
    new_path = f"{AGENT_PRESET_ROOT}/qa-triage-parent-9000/preset.yml"

    assert old_path in projection.files
    assert new_path not in projection.files
    parent_spec = yaml.safe_load(projection.files[old_path])
    assert parent_spec["id"] == "qa-triage-parent"
    assert parent_spec["slug"] == "qa-triage-parent-9000"
    assert parent_spec["name"] == "QA triage parent 9000"


@pytest.mark.anyio
async def test_project_workspace_preserves_table_source_id_after_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_full_git_tree(include_schedules=False),
        commit_sha="f" * 40,
    )

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    await service.project_workspace()
    table = await session.scalar(
        select(Table).where(
            Table.workspace_id == svc_role.workspace_id,
            Table.name == "qa_indicators",
        )
    )
    assert table is not None

    await BaseTablesService(session=session, role=svc_role).update_table(
        table,
        TableUpdate(name="qa_indicators_renamed"),
    )

    projection = await service.project_workspace(create_missing_mappings=False)
    old_path = f"{TABLE_ROOT}/qa_indicators/table.yml"
    new_path = f"{TABLE_ROOT}/qa_indicators_renamed/table.yml"

    assert old_path in projection.files
    assert new_path not in projection.files
    table_spec = yaml.safe_load(projection.files[old_path])
    assert table_spec["id"] == "qa_indicators"
    assert table_spec["name"] == "qa_indicators_renamed"


@pytest.mark.anyio
async def test_project_workspace_preserves_skill_source_id_after_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_selected_git_tree(),
        commit_sha="o" * 40,
    )

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    await service.project_workspace()
    skill = await session.scalar(
        select(Skill).where(
            Skill.workspace_id == svc_role.workspace_id,
            Skill.name == "qa-enrichment-skill",
        )
    )
    assert skill is not None

    skill.name = "qa-enrichment-restored"
    session.add(skill)
    await session.flush()

    projection = await service.project_workspace(create_missing_mappings=False)
    old_path = f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml"
    new_path = f"{SKILL_ROOT}/qa-enrichment-restored/skill.yml"

    assert old_path in projection.files
    assert new_path not in projection.files
    skill_spec = yaml.safe_load(projection.files[old_path])
    assert skill_spec["id"] == "qa-enrichment-skill"
    assert skill_spec["slug"] == "qa-enrichment-restored"
    assert skill_spec["name"] == "QA enrichment skill"


@pytest.mark.anyio
async def test_project_workspace_preserves_workflow_source_id_after_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.return_value = VcsTreeSnapshot(
        commit_sha="u" * 40,
        tree_sha="tree-1",
        files=_workflow_git_tree(
            source_id="qa-workflow",
            alias="qa-workflow",
            title="QA workflow",
        ),
    )
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
            AsyncMock(return_value=RegistryLock(origins={}, actions={})),
        ),
    ):
        pull_result = await service.pull(options=PullOptions(commit_sha="u" * 40))

    assert pull_result.success is True
    workflow = await session.scalar(
        select(Workflow).where(
            Workflow.workspace_id == svc_role.workspace_id,
            Workflow.alias == "qa-workflow",
        )
    )
    assert workflow is not None
    workflow.alias = "qa-workflow-renamed"
    session.add(workflow)
    await session.flush()

    projection = await service.project_workspace(create_missing_mappings=False)
    old_path = f"{WORKFLOW_ROOT}/qa-workflow/definition.yml"
    new_path = f"{WORKFLOW_ROOT}/qa-workflow-renamed/definition.yml"

    assert old_path in projection.files
    assert new_path not in projection.files
    workflow_spec = yaml.safe_load(projection.files[old_path])
    assert workflow_spec["id"] == "qa-workflow"
    assert workflow_spec["alias"] == "qa-workflow-renamed"


@pytest.mark.anyio
async def test_project_workspace_preserves_mapped_source_ids_after_renames(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    snapshot, diagnostics = await service.parse_files(
        _expanded_full_git_tree(include_schedules=False),
        commit_sha="p" * 40,
    )

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await service.project_workspace()

    tag = await session.scalar(
        select(CaseTag).where(
            CaseTag.workspace_id == svc_role.workspace_id,
            CaseTag.ref == "qa-alert",
        )
    )
    dropdown = await session.scalar(
        select(CaseDropdownDefinition).where(
            CaseDropdownDefinition.workspace_id == svc_role.workspace_id,
            CaseDropdownDefinition.ref == "qa_resolution_reason",
        )
    )
    duration = await session.scalar(
        select(CaseDurationDefinition).where(
            CaseDurationDefinition.workspace_id == svc_role.workspace_id,
            CaseDurationDefinition.name == "qa_time_to_triage",
        )
    )
    variable = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == svc_role.workspace_id,
            WorkspaceVariable.name == "qa_config",
            WorkspaceVariable.environment == "default",
        )
    )
    secret = await session.scalar(
        select(Secret).where(
            Secret.workspace_id == svc_role.workspace_id,
            Secret.name == "qa_threatintel",
            Secret.environment == "default",
        )
    )
    case_fields = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == svc_role.workspace_id)
    )
    assert tag is not None
    assert dropdown is not None
    assert duration is not None
    assert variable is not None
    assert secret is not None
    assert case_fields is not None

    tag.name = "QA alert renamed"
    tag.ref = "qa-alert-renamed"
    dropdown.name = "QA resolution reason renamed"
    dropdown.ref = "qa_resolution_reason_renamed"
    duration.name = "qa_time_to_triage_renamed"
    variable.name = "qa_config_renamed"
    secret.name = "qa_threatintel_renamed"
    case_schema = dict(case_fields.schema or {})
    case_schema["qa_vendor_ref"] = case_schema.pop("qa_external_ref")
    case_fields.schema = case_schema
    case_field_mapping = await session.scalar(
        select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
            WorkspaceSyncResourceMapping.resource_type
            == SyncResourceType.CASE_FIELD.value,
            WorkspaceSyncResourceMapping.source_id == "qa_external_ref",
        )
    )
    assert case_field_mapping is not None
    case_field_mapping.local_id = uuid.uuid5(case_fields.id, "qa_vendor_ref")
    session.add_all([tag, dropdown, duration, variable, secret, case_fields])
    session.add(case_field_mapping)
    await session.flush()

    projection = await service.project_workspace(create_missing_mappings=False)

    tag_spec = yaml.safe_load(projection.files[f"{CASE_TAG_ROOT}/qa-alert.yml"])
    assert f"{CASE_TAG_ROOT}/qa-alert-renamed.yml" not in projection.files
    assert tag_spec["id"] == "qa-alert"
    assert tag_spec["name"] == "QA alert renamed"

    dropdown_spec = yaml.safe_load(
        projection.files[f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml"]
    )
    assert (
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason_renamed.yml" not in projection.files
    )
    assert dropdown_spec["id"] == "qa_resolution_reason"
    assert dropdown_spec["name"] == "QA resolution reason renamed"

    duration_spec = yaml.safe_load(
        projection.files[f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml"]
    )
    assert f"{CASE_DURATION_ROOT}/qa_time_to_triage_renamed.yml" not in projection.files
    assert duration_spec["id"] == "qa_time_to_triage"
    assert duration_spec["name"] == "qa_time_to_triage_renamed"

    variable_spec = yaml.safe_load(
        projection.files[f"{VARIABLE_ROOT}/default/qa_config.yml"]
    )
    assert f"{VARIABLE_ROOT}/default/qa_config_renamed.yml" not in projection.files
    assert variable_spec["id"] == "default/qa_config"
    assert variable_spec["name"] == "qa_config_renamed"

    secret_spec = yaml.safe_load(
        projection.files[f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml"]
    )
    assert (
        f"{SECRET_METADATA_ROOT}/default/qa_threatintel_renamed.yml"
        not in projection.files
    )
    assert secret_spec["id"] == "default/qa_threatintel"
    assert secret_spec["name"] == "qa_threatintel_renamed"

    case_field_spec = yaml.safe_load(
        projection.files[f"{CASE_FIELD_ROOT}/qa_external_ref.yml"]
    )
    assert f"{CASE_FIELD_ROOT}/qa_vendor_ref.yml" not in projection.files
    assert case_field_spec["id"] == "qa_external_ref"
    assert case_field_spec["name"] == "qa_vendor_ref"


@pytest.mark.anyio
async def test_pull_table_rename_reuses_source_id_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="m" * 40,
            tree_sha="tree-1",
            files=_table_git_tree(
                source_id="qa_indicators",
                name="qa_indicators",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="n" * 40,
            tree_sha="tree-2",
            files=_table_git_tree(
                source_id="qa_indicators",
                name="qa_indicators_renamed",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="m" * 40))

        assert first_result.success is True
        first_table = await session.scalar(
            select(Table).where(
                Table.workspace_id == svc_role.workspace_id,
                Table.name == "qa_indicators",
            )
        )
        assert first_table is not None
        first_table_id = first_table.id

        second_result = await service.pull(options=PullOptions(commit_sha="n" * 40))

    assert second_result.success is True
    tables = list(
        (
            await session.scalars(
                select(Table).where(
                    Table.workspace_id == svc_role.workspace_id,
                )
            )
        ).all()
    )
    assert len(tables) == 1
    assert tables[0].id == first_table_id
    assert tables[0].name == "qa_indicators_renamed"
    mapping = await session.scalar(
        select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
            WorkspaceSyncResourceMapping.resource_type == SyncResourceType.TABLE.value,
            WorkspaceSyncResourceMapping.source_id == "qa_indicators",
        )
    )
    assert mapping is not None
    assert mapping.local_id == first_table_id


@pytest.mark.anyio
async def test_pull_table_source_id_rename_reassigns_existing_local_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="u" * 40,
            tree_sha="tree-1",
            files=_table_git_tree(
                source_id="qa_indicators",
                name="qa_indicators",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="v" * 40,
            tree_sha="tree-2",
            files=_table_git_tree(
                source_id="qa_indicators_v2",
                name="qa_indicators",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="u" * 40))

        assert first_result.success is True
        first_table = await session.scalar(
            select(Table).where(
                Table.workspace_id == svc_role.workspace_id,
                Table.name == "qa_indicators",
            )
        )
        assert first_table is not None
        first_table_id = first_table.id

        second_result = await service.pull(options=PullOptions(commit_sha="v" * 40))

    assert second_result.success is True
    tables = list(
        (
            await session.scalars(
                select(Table).where(
                    Table.workspace_id == svc_role.workspace_id,
                )
            )
        ).all()
    )
    assert len(tables) == 1
    assert tables[0].id == first_table_id
    mappings = list(
        (
            await session.scalars(
                select(WorkspaceSyncResourceMapping).where(
                    WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
                    WorkspaceSyncResourceMapping.resource_type
                    == SyncResourceType.TABLE.value,
                )
            )
        ).all()
    )
    assert len(mappings) == 1
    assert mappings[0].source_id == "qa_indicators_v2"
    assert mappings[0].source_path == f"{TABLE_ROOT}/qa_indicators_v2/table.yml"
    assert mappings[0].local_id == first_table_id


@pytest.mark.anyio
async def test_pull_table_name_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="o" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _table_git_tree(source_id="table-a", name="alpha_table"),
                _table_git_tree(source_id="table-b", name="beta_table"),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="p" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _table_git_tree(source_id="table-a", name="beta_table"),
                _table_git_tree(source_id="table-b", name="alpha_table"),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="o" * 40))
        alpha_id = await session.scalar(
            select(Table.id).where(
                Table.workspace_id == svc_role.workspace_id,
                Table.name == "alpha_table",
            )
        )
        beta_id = await session.scalar(
            select(Table.id).where(
                Table.workspace_id == svc_role.workspace_id,
                Table.name == "beta_table",
            )
        )
        second_result = await service.pull(options=PullOptions(commit_sha="p" * 40))

    assert first_result.success is True
    assert second_result.success is True
    assert alpha_id is not None
    assert beta_id is not None
    tables = {
        table.name: table.id
        for table in (
            await session.scalars(
                select(Table).where(Table.workspace_id == svc_role.workspace_id)
            )
        ).all()
    }
    assert tables == {"alpha_table": beta_id, "beta_table": alpha_id}


@pytest.mark.anyio
async def test_pull_agent_preset_slug_rename_reuses_source_id_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="f" * 40,
            tree_sha="tree-1",
            files=_agent_preset_git_tree(
                source_id="qa-identity",
                slug="qa-identity",
                name="QA identity",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="g" * 40,
            tree_sha="tree-2",
            files=_agent_preset_git_tree(
                source_id="qa-identity",
                slug="qa-identity-renamed",
                name="QA identity renamed",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="f" * 40))

        assert first_result.success is True
        first_preset = await session.scalar(
            select(AgentPreset).where(
                AgentPreset.workspace_id == svc_role.workspace_id,
                AgentPreset.slug == "qa-identity",
            )
        )
        assert first_preset is not None
        first_preset_id = first_preset.id

        second_result = await service.pull(options=PullOptions(commit_sha="g" * 40))

    assert second_result.success is True
    presets = list(
        (
            await session.scalars(
                select(AgentPreset).where(
                    AgentPreset.workspace_id == svc_role.workspace_id,
                )
            )
        ).all()
    )
    assert len(presets) == 1
    assert presets[0].id == first_preset_id
    assert presets[0].slug == "qa-identity-renamed"
    assert presets[0].name == "QA identity renamed"


@pytest.mark.anyio
async def test_pull_unpublished_agent_preset_clears_current_version(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    unpublished_files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{AGENT_PRESET_ROOT}/qa-draft/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "qa-draft",
                "slug": "qa-draft",
                "name": "QA draft",
                "current_version": None,
            }
        ),
    }
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="d" * 40,
            tree_sha="tree-1",
            files=_agent_preset_git_tree(
                source_id="qa-draft",
                slug="qa-draft",
                name="QA draft",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="e" * 40,
            tree_sha="tree-2",
            files=unpublished_files,
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="d" * 40))
        second_result = await service.pull(options=PullOptions(commit_sha="e" * 40))

    assert first_result.success is True
    assert second_result.success is True
    preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "qa-draft",
        )
    )
    assert preset is not None
    assert preset.current_version_id is None


@pytest.mark.anyio
async def test_pull_unversioned_skill_clears_current_version(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    unversioned_files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml": _yaml(
            {
                "version": 1,
                "type": "skill",
                "id": "qa-enrichment-skill",
                "slug": "qa-enrichment-skill",
                "name": "QA enrichment skill",
                "description": "Deterministic enrichment helper",
                "current_version": None,
            }
        ),
    }
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="d" * 40,
            tree_sha="tree-1",
            files=_skill_git_tree(
                source_id="qa-enrichment-skill",
                slug="qa-enrichment-skill",
                name="QA enrichment skill",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="e" * 40,
            tree_sha="tree-2",
            files=unversioned_files,
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    async def draft_paths_for(skill_id: uuid.UUID) -> list[str]:
        return list(
            (
                await session.scalars(
                    select(SkillDraftFile.path)
                    .where(
                        SkillDraftFile.workspace_id == svc_role.workspace_id,
                        SkillDraftFile.skill_id == skill_id,
                    )
                    .order_by(SkillDraftFile.path.asc())
                )
            ).all()
        )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="d" * 40))
        skill = await session.scalar(
            select(Skill).where(
                Skill.workspace_id == svc_role.workspace_id,
                Skill.name == "qa-enrichment-skill",
            )
        )
        assert skill is not None
        assert skill.current_version_id is not None
        assert await draft_paths_for(skill.id) == ["SKILL.md"]
        second_result = await service.pull(options=PullOptions(commit_sha="e" * 40))

    assert first_result.success is True
    assert second_result.success is True
    skill = await session.scalar(
        select(Skill).where(
            Skill.workspace_id == svc_role.workspace_id,
            Skill.name == "qa-enrichment-skill",
        )
    )
    assert skill is not None
    assert skill.current_version_id is None
    assert await draft_paths_for(skill.id) == []


@pytest.mark.anyio
async def test_pull_agent_preset_slug_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="a" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _agent_preset_git_tree(
                    source_id="preset-a",
                    slug="alpha",
                    name="Alpha",
                ),
                _agent_preset_git_tree(
                    source_id="preset-b",
                    slug="beta",
                    name="Beta",
                ),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="b" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _agent_preset_git_tree(
                    source_id="preset-a",
                    slug="beta",
                    name="Beta",
                ),
                _agent_preset_git_tree(
                    source_id="preset-b",
                    slug="alpha",
                    name="Alpha",
                ),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="a" * 40))
        alpha_id = await session.scalar(
            select(AgentPreset.id).where(
                AgentPreset.workspace_id == svc_role.workspace_id,
                AgentPreset.slug == "alpha",
            )
        )
        beta_id = await session.scalar(
            select(AgentPreset.id).where(
                AgentPreset.workspace_id == svc_role.workspace_id,
                AgentPreset.slug == "beta",
            )
        )
        second_result = await service.pull(options=PullOptions(commit_sha="b" * 40))

    assert first_result.success is True
    assert second_result.success is True
    assert alpha_id is not None
    assert beta_id is not None
    presets = {
        preset.slug: preset.id
        for preset in (
            await session.scalars(
                select(AgentPreset).where(
                    AgentPreset.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    }
    assert presets == {"alpha": beta_id, "beta": alpha_id}


@pytest.mark.anyio
async def test_pull_skill_slug_rename_reuses_source_id_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="h" * 40,
            tree_sha="tree-1",
            files=_skill_git_tree(
                source_id="qa-enrichment-skill",
                slug="qa-enrichment-skill",
                name="QA enrichment skill",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="i" * 40,
            tree_sha="tree-2",
            files=_skill_git_tree(
                source_id="qa-enrichment-skill",
                slug="qa-enrichment-restored",
                name="QA enrichment restored",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="h" * 40))
        assert first_result.success is True
        first_skill = await session.scalar(
            select(Skill).where(
                Skill.workspace_id == svc_role.workspace_id,
                Skill.name == "qa-enrichment-skill",
            )
        )
        assert first_skill is not None
        first_skill_id = first_skill.id

        second_result = await service.pull(options=PullOptions(commit_sha="i" * 40))

    assert second_result.success is True
    skills = list(
        (
            await session.scalars(
                select(Skill).where(Skill.workspace_id == svc_role.workspace_id)
            )
        ).all()
    )
    assert len(skills) == 1
    assert skills[0].id == first_skill_id
    assert skills[0].name == "qa-enrichment-restored"
    version = await session.scalar(
        select(SkillVersion).where(
            SkillVersion.workspace_id == svc_role.workspace_id,
            SkillVersion.skill_id == first_skill_id,
            SkillVersion.version == 1,
        )
    )
    assert version is not None
    assert version.name == "QA enrichment restored"


@pytest.mark.anyio
async def test_import_skill_version_rejects_missing_declared_file_content(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    spec = WorkspaceSpec(
        skills={
            "qa-enrichment-skill": SkillResourceSpec(
                id="qa-enrichment-skill",
                slug="qa-enrichment-skill",
                name="QA enrichment skill",
                current_version=1,
                versions={
                    1: SkillVersionResourceSpec(
                        version_number=1,
                        name="QA enrichment skill",
                        files=[
                            SkillFileSpec(
                                path="SKILL.md",
                                sha256=hashlib.sha256(b"missing").hexdigest(),
                            )
                        ],
                        file_contents={},
                    )
                },
            )
        }
    )

    with pytest.raises(TracecatValidationError, match="no content was provided"):
        await WorkspaceResourceImportService(
            session=session,
            role=svc_role,
        ).import_non_workflow_resources(spec)


@pytest.mark.anyio
async def test_project_workspace_preserves_binary_skill_version_file(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    binary_content = b"\x89PNG\r\n\x1a\n\xff\x00"
    encoded_content = base64.b64encode(binary_content).decode("ascii")
    binary_sha256 = hashlib.sha256(binary_content).hexdigest()
    spec = WorkspaceSpec(
        skills={
            "binary-skill": SkillResourceSpec(
                id="binary-skill",
                slug="binary-skill",
                name="Binary skill",
                current_version=1,
                versions={
                    1: SkillVersionResourceSpec(
                        version_number=1,
                        name="Binary skill",
                        files=[
                            SkillFileSpec(
                                path="assets/logo.png",
                                sha256=binary_sha256,
                                encoding="base64",
                            )
                        ],
                        file_contents={
                            "assets/logo.png": f"{encoded_content[:4]}\n{encoded_content[4:]}\n"
                        },
                    )
                },
            )
        }
    )

    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(spec)

    projection = await WorkspaceSyncService(
        session=session,
        role=svc_role,
    ).project_workspace()

    version_path = f"{SKILL_ROOT}/binary-skill/versions/1/version.yml"
    file_path = f"{SKILL_ROOT}/binary-skill/versions/1/files/assets/logo.png"
    version_spec = yaml.safe_load(projection.files[version_path])
    assert version_spec["files"] == [
        {
            "path": "assets/logo.png",
            "sha256": binary_sha256,
            "encoding": "base64",
        }
    ]
    assert projection.files[file_path] == encoded_content


@pytest.mark.anyio
async def test_pull_skill_slug_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="s" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _skill_git_tree(
                    source_id="skill-a",
                    slug="alpha-skill",
                    name="Alpha skill",
                ),
                _skill_git_tree(
                    source_id="skill-b",
                    slug="beta-skill",
                    name="Beta skill",
                ),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="t" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _skill_git_tree(
                    source_id="skill-a",
                    slug="beta-skill",
                    name="Beta skill",
                ),
                _skill_git_tree(
                    source_id="skill-b",
                    slug="alpha-skill",
                    name="Alpha skill",
                ),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="s" * 40))
        alpha_id = await session.scalar(
            select(Skill.id).where(
                Skill.workspace_id == svc_role.workspace_id,
                Skill.name == "alpha-skill",
            )
        )
        beta_id = await session.scalar(
            select(Skill.id).where(
                Skill.workspace_id == svc_role.workspace_id,
                Skill.name == "beta-skill",
            )
        )
        second_result = await service.pull(options=PullOptions(commit_sha="t" * 40))

    assert first_result.success is True
    assert second_result.success is True
    assert alpha_id is not None
    assert beta_id is not None
    skills = {
        skill.name: skill.id
        for skill in (
            await session.scalars(
                select(Skill).where(Skill.workspace_id == svc_role.workspace_id)
            )
        ).all()
    }
    assert skills == {"alpha-skill": beta_id, "beta-skill": alpha_id}


@pytest.mark.anyio
async def test_pull_workflow_alias_rename_reuses_source_id_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="q" * 40,
            tree_sha="tree-1",
            files=_workflow_git_tree(
                source_id="qa-workflow",
                alias="qa-workflow",
                title="QA workflow",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="r" * 40,
            tree_sha="tree-2",
            files=_workflow_git_tree(
                source_id="qa-workflow",
                alias="qa-workflow-renamed",
                title="QA workflow renamed",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
            AsyncMock(return_value=RegistryLock(origins={}, actions={})),
        ),
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="q" * 40))
        assert first_result.success is True
        first_workflow = await session.scalar(
            select(Workflow).where(
                Workflow.workspace_id == svc_role.workspace_id,
                Workflow.alias == "qa-workflow",
            )
        )
        assert first_workflow is not None
        first_workflow_id = first_workflow.id

        second_result = await service.pull(options=PullOptions(commit_sha="r" * 40))

    assert second_result.success is True
    workflows = list(
        (
            await session.scalars(
                select(Workflow).where(Workflow.workspace_id == svc_role.workspace_id)
            )
        ).all()
    )
    assert len(workflows) == 1
    assert workflows[0].id == first_workflow_id
    assert workflows[0].alias == "qa-workflow-renamed"


@pytest.mark.anyio
async def test_pull_workflow_source_id_rename_reuses_alias_mapping(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="y" * 40,
            tree_sha="tree-1",
            files=_workflow_git_tree(
                source_id="old-triage",
                alias="triage-alert",
                title="Triage alert",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="z" * 40,
            tree_sha="tree-2",
            files=_workflow_git_tree(
                source_id="new-triage",
                alias="triage-alert",
                title="Triage alert",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
            AsyncMock(return_value=RegistryLock(origins={}, actions={})),
        ),
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="y" * 40))
        assert first_result.success is True
        first_workflow = await session.scalar(
            select(Workflow).where(
                Workflow.workspace_id == svc_role.workspace_id,
                Workflow.alias == "triage-alert",
            )
        )
        assert first_workflow is not None
        first_workflow_id = first_workflow.id

        second_result = await service.pull(options=PullOptions(commit_sha="z" * 40))

    assert second_result.success is True
    workflows = list(
        (
            await session.scalars(
                select(Workflow).where(Workflow.workspace_id == svc_role.workspace_id)
            )
        ).all()
    )
    assert len(workflows) == 1
    assert workflows[0].id == first_workflow_id
    assert workflows[0].alias == "triage-alert"
    assert (
        await _mapping_for(
            session,
            role=svc_role,
            resource_type=SyncResourceType.WORKFLOW,
            source_id="old-triage",
        )
        is None
    )
    await _assert_mapping_targets(
        session,
        role=svc_role,
        resource_type=SyncResourceType.WORKFLOW,
        source_id="new-triage",
        local_id=first_workflow_id,
    )


@pytest.mark.anyio
async def test_pull_workflow_alias_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="w" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _workflow_git_tree(
                    source_id="workflow-a",
                    alias="alpha-workflow",
                    title="Alpha workflow",
                ),
                _workflow_git_tree(
                    source_id="workflow-b",
                    alias="beta-workflow",
                    title="Beta workflow",
                ),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="x" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _workflow_git_tree(
                    source_id="workflow-a",
                    alias="beta-workflow",
                    title="Beta workflow",
                ),
                _workflow_git_tree(
                    source_id="workflow-b",
                    alias="alpha-workflow",
                    title="Alpha workflow",
                ),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with (
        patch(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            return_value=transport,
        ),
        patch(
            "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
            AsyncMock(return_value=RegistryLock(origins={}, actions={})),
        ),
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="w" * 40))
        alpha_id = await session.scalar(
            select(Workflow.id).where(
                Workflow.workspace_id == svc_role.workspace_id,
                Workflow.alias == "alpha-workflow",
            )
        )
        beta_id = await session.scalar(
            select(Workflow.id).where(
                Workflow.workspace_id == svc_role.workspace_id,
                Workflow.alias == "beta-workflow",
            )
        )
        second_result = await service.pull(options=PullOptions(commit_sha="x" * 40))

    assert first_result.success is True
    assert second_result.success is True
    assert alpha_id is not None
    assert beta_id is not None
    workflows = {
        workflow.alias: workflow.id
        for workflow in (
            await session.scalars(
                select(Workflow).where(Workflow.workspace_id == svc_role.workspace_id)
            )
        ).all()
    }
    assert workflows == {"alpha-workflow": beta_id, "beta-workflow": alpha_id}


@pytest.mark.anyio
async def test_pull_case_duration_name_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="d" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _case_duration_git_tree(
                    source_id="duration-a",
                    name="alpha_duration",
                ),
                _case_duration_git_tree(
                    source_id="duration-b",
                    name="beta_duration",
                ),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="e" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _case_duration_git_tree(
                    source_id="duration-a",
                    name="beta_duration",
                ),
                _case_duration_git_tree(
                    source_id="duration-b",
                    name="alpha_duration",
                ),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="d" * 40))
        alpha_id = await session.scalar(
            select(CaseDurationDefinition.id).where(
                CaseDurationDefinition.workspace_id == svc_role.workspace_id,
                CaseDurationDefinition.name == "alpha_duration",
            )
        )
        beta_id = await session.scalar(
            select(CaseDurationDefinition.id).where(
                CaseDurationDefinition.workspace_id == svc_role.workspace_id,
                CaseDurationDefinition.name == "beta_duration",
            )
        )
        second_result = await service.pull(options=PullOptions(commit_sha="e" * 40))

    assert first_result.success is True
    assert second_result.success is True
    assert alpha_id is not None
    assert beta_id is not None
    durations = {
        duration.name: duration.id
        for duration in (
            await session.scalars(
                select(CaseDurationDefinition).where(
                    CaseDurationDefinition.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    }
    assert durations == {"alpha_duration": beta_id, "beta_duration": alpha_id}


@pytest.mark.anyio
async def test_pull_case_dropdown_ref_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="h" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _case_dropdown_git_tree(
                    source_id="alpha_reason",
                    name="Alpha reason",
                ),
                _case_dropdown_git_tree(
                    source_id="beta_reason",
                    name="Beta reason",
                ),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="i" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _case_dropdown_git_tree(
                    source_id="alpha_reason",
                    name="Alpha reason",
                ),
                _case_dropdown_git_tree(
                    source_id="beta_reason",
                    name="Beta reason",
                ),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="h" * 40))
        assert first_result.success is True
        alpha = await session.scalar(
            select(CaseDropdownDefinition).where(
                CaseDropdownDefinition.workspace_id == svc_role.workspace_id,
                CaseDropdownDefinition.ref == "alpha_reason",
            )
        )
        beta = await session.scalar(
            select(CaseDropdownDefinition).where(
                CaseDropdownDefinition.workspace_id == svc_role.workspace_id,
                CaseDropdownDefinition.ref == "beta_reason",
            )
        )
        assert alpha is not None
        assert beta is not None
        alpha_id, beta_id = alpha.id, beta.id

        # Swap the refs locally between pulls, parking one row under a temporary
        # ref so this setup does not itself trip the unique constraint. The
        # mappings still resolve "alpha_reason" -> alpha and "beta_reason" -> beta.
        alpha.ref = "tmp_swap_reason"
        session.add(alpha)
        await session.flush()
        beta.ref = "alpha_reason"
        session.add(beta)
        await session.flush()
        alpha.ref = "beta_reason"
        session.add(alpha)
        await session.flush()

        # The second pull restores each row to its mapped ref. Without a parking
        # phase, writing "alpha_reason" back onto alpha collides with beta, which
        # still owns it mid-loop.
        second_result = await service.pull(options=PullOptions(commit_sha="i" * 40))

    assert second_result.success is True
    dropdowns = {
        dropdown.ref: dropdown.id
        for dropdown in (
            await session.scalars(
                select(CaseDropdownDefinition).where(
                    CaseDropdownDefinition.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    }
    assert dropdowns == {"alpha_reason": alpha_id, "beta_reason": beta_id}


@pytest.mark.anyio
async def test_pull_case_field_name_swap_reuses_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="f" * 40,
            tree_sha="tree-1",
            files=_combined_git_tree(
                _case_field_git_tree(source_id="field-a", name="alpha_field"),
                _case_field_git_tree(source_id="field-b", name="beta_field"),
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="g" * 40,
            tree_sha="tree-2",
            files=_combined_git_tree(
                _case_field_git_tree(source_id="field-a", name="beta_field"),
                _case_field_git_tree(source_id="field-b", name="alpha_field"),
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="f" * 40))
        definition = await session.scalar(
            select(CaseFields).where(CaseFields.workspace_id == svc_role.workspace_id)
        )
        assert definition is not None
        schema_id = definition.id
        first_local_ids = {
            "field-a": uuid.uuid5(schema_id, "alpha_field"),
            "field-b": uuid.uuid5(schema_id, "beta_field"),
        }
        second_result = await service.pull(options=PullOptions(commit_sha="g" * 40))

    assert first_result.success is True
    assert second_result.success is True
    definition = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == svc_role.workspace_id)
    )
    assert definition is not None
    assert set(definition.schema) == {"alpha_field", "beta_field"}
    mappings = {
        mapping.source_id: mapping.local_id
        for mapping in (
            await session.scalars(
                select(WorkspaceSyncResourceMapping).where(
                    WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
                    WorkspaceSyncResourceMapping.resource_type
                    == SyncResourceType.CASE_FIELD.value,
                )
            )
        ).all()
    }
    assert mappings["field-a"] == uuid.uuid5(schema_id, "beta_field")
    assert mappings["field-b"] == uuid.uuid5(schema_id, "alpha_field")
    assert mappings != first_local_ids


@pytest.mark.anyio
async def test_pull_simple_resource_renames_reuse_source_id_mappings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="s" * 40,
            tree_sha="tree-1",
            files=_simple_resource_rename_git_tree(
                tag_name="QA alert",
                dropdown_name="QA resolution reason",
                duration_name="QA time to triage",
                variable_name="qa_config",
                secret_name="qa_threatintel",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="t" * 40,
            tree_sha="tree-2",
            files=_simple_resource_rename_git_tree(
                tag_name="QA alert renamed",
                dropdown_name="QA resolution reason renamed",
                duration_name="QA time to triage renamed",
                variable_name="qa_config_renamed",
                secret_name="qa_threatintel_renamed",
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="s" * 40))
        assert first_result.success is True
        first_tag = await session.scalar(
            select(CaseTag).where(
                CaseTag.workspace_id == svc_role.workspace_id,
                CaseTag.ref == "qa-alert",
            )
        )
        first_dropdown = await session.scalar(
            select(CaseDropdownDefinition).where(
                CaseDropdownDefinition.workspace_id == svc_role.workspace_id,
                CaseDropdownDefinition.ref == "qa_resolution_reason",
            )
        )
        first_duration = await session.scalar(
            select(CaseDurationDefinition).where(
                CaseDurationDefinition.workspace_id == svc_role.workspace_id,
                CaseDurationDefinition.name == "QA time to triage",
            )
        )
        first_variable = await session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == svc_role.workspace_id,
                WorkspaceVariable.name == "qa_config",
                WorkspaceVariable.environment == "default",
            )
        )
        first_secret = await session.scalar(
            select(Secret).where(
                Secret.workspace_id == svc_role.workspace_id,
                Secret.name == "qa_threatintel",
                Secret.environment == "default",
            )
        )
        assert first_tag is not None
        assert first_dropdown is not None
        assert first_duration is not None
        assert first_variable is not None
        assert first_secret is not None
        first_ids = {
            "tag": first_tag.id,
            "dropdown": first_dropdown.id,
            "duration": first_duration.id,
            "variable": first_variable.id,
            "secret": first_secret.id,
        }

        second_result = await service.pull(options=PullOptions(commit_sha="t" * 40))

    assert second_result.success is True
    tags = list(
        (
            await session.scalars(
                select(CaseTag).where(CaseTag.workspace_id == svc_role.workspace_id)
            )
        ).all()
    )
    dropdowns = list(
        (
            await session.scalars(
                select(CaseDropdownDefinition).where(
                    CaseDropdownDefinition.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    )
    durations = list(
        (
            await session.scalars(
                select(CaseDurationDefinition).where(
                    CaseDurationDefinition.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    )
    variables = list(
        (
            await session.scalars(
                select(WorkspaceVariable).where(
                    WorkspaceVariable.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    )
    secrets = list(
        (
            await session.scalars(
                select(Secret).where(Secret.workspace_id == svc_role.workspace_id)
            )
        ).all()
    )

    assert len(tags) == 1
    assert tags[0].id == first_ids["tag"]
    assert tags[0].name == "QA alert renamed"
    assert tags[0].ref == "qa-alert"
    assert len(dropdowns) == 1
    assert dropdowns[0].id == first_ids["dropdown"]
    assert dropdowns[0].name == "QA resolution reason renamed"
    assert dropdowns[0].ref == "qa_resolution_reason"
    assert len(durations) == 1
    assert durations[0].id == first_ids["duration"]
    assert durations[0].name == "QA time to triage renamed"
    assert len(variables) == 1
    assert variables[0].id == first_ids["variable"]
    assert variables[0].name == "qa_config_renamed"
    assert variables[0].environment == "default"
    assert len(secrets) == 1
    assert secrets[0].id == first_ids["secret"]
    assert secrets[0].name == "qa_threatintel_renamed"
    assert secrets[0].environment == "default"


@pytest.mark.anyio
async def test_agent_preset_import_resolves_parent_before_child_order(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{AGENT_PRESET_ROOT}/a-parent/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "a-parent",
                "slug": "a-parent",
                "name": "A parent",
                "current_version": 1,
            }
        ),
        f"{AGENT_PRESET_ROOT}/a-parent/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "A parent",
                "subagents": [{"slug": "z-child"}],
            }
        ),
        f"{AGENT_PRESET_ROOT}/z-child/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "z-child",
                "slug": "z-child",
                "name": "Z child",
                "current_version": 1,
            }
        ),
        f"{AGENT_PRESET_ROOT}/z-child/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "Z child",
            }
        ),
    }
    snapshot, diagnostics = await service.parse_files(files, commit_sha="h" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    parent = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "a-parent",
        )
    )
    child = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "z-child",
        )
    )
    assert parent is not None
    assert child is not None
    assert child.current_version_id is not None
    assert parent.agents["enabled"] is True
    assert parent.agents["subagents"][0]["preset_version_id"] == str(
        child.current_version_id
    )


@pytest.mark.anyio
async def test_agent_preset_sync_preserves_subagent_options(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{AGENT_PRESET_ROOT}/a-parent/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "a-parent",
                "slug": "a-parent",
                "name": "A parent",
                "current_version": 1,
            }
        ),
        f"{AGENT_PRESET_ROOT}/a-parent/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "A parent",
                "subagents": [
                    {
                        "slug": "z-child",
                        "version": 1,
                        "name": "evidence-child",
                        "description": "Collect evidence",
                        "max_turns": 3,
                    }
                ],
            }
        ),
        f"{AGENT_PRESET_ROOT}/z-child/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "z-child",
                "slug": "z-child",
                "name": "Z child",
                "current_version": 1,
            }
        ),
        f"{AGENT_PRESET_ROOT}/z-child/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "Z child",
            }
        ),
    }
    snapshot, diagnostics = await service.parse_files(files, commit_sha="j" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    parent = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "a-parent",
        )
    )
    child = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "z-child",
        )
    )
    assert parent is not None
    assert child is not None
    assert child.current_version_id is not None
    imported_subagent = parent.agents["subagents"][0]
    assert imported_subagent["preset_version_id"] == str(child.current_version_id)
    assert imported_subagent["preset_version"] == 1
    assert imported_subagent["name"] == "evidence-child"
    assert imported_subagent["description"] == "Collect evidence"
    assert imported_subagent["max_turns"] == 3

    parent_version_row = await session.scalar(
        select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == svc_role.workspace_id,
            AgentPresetVersion.preset_id == parent.id,
            AgentPresetVersion.version == 1,
        )
    )
    assert parent_version_row is not None
    assert parent.subagents_enabled is True
    assert parent_version_row.subagents_enabled is True
    head_edge = await session.scalar(
        select(AgentPresetSubagent).where(
            AgentPresetSubagent.workspace_id == svc_role.workspace_id,
            AgentPresetSubagent.parent_preset_id == parent.id,
        )
    )
    version_edge = await session.scalar(
        select(AgentPresetVersionSubagent).where(
            AgentPresetVersionSubagent.workspace_id == svc_role.workspace_id,
            AgentPresetVersionSubagent.parent_preset_version_id
            == parent_version_row.id,
        )
    )
    assert head_edge is not None
    assert version_edge is not None
    assert head_edge.child_preset_id == child.id
    assert version_edge.child_preset_id == child.id
    assert version_edge.alias == "evidence-child"
    assert version_edge.description == "Collect evidence"
    assert version_edge.max_turns == 3

    runtime_binding = await AgentPresetService(
        session=session,
        role=svc_role,
    ).get_version_subagent_binding(parent_version_row)
    assert [subagent.preset_id for subagent in runtime_binding.subagents] == [child.id]

    projection = await service.project_workspace(create_missing_mappings=False)
    parent_spec = yaml.safe_load(
        projection.files[f"{AGENT_PRESET_ROOT}/a-parent/preset.yml"]
    )
    assert "subagents" not in parent_spec
    parent_version = yaml.safe_load(
        projection.files[f"{AGENT_PRESET_ROOT}/a-parent/versions/1.yml"]
    )
    assert parent_version["subagents"] == [
        {
            "slug": "z-child",
            "version": 1,
            "name": "evidence-child",
            "description": "Collect evidence",
            "max_turns": 3,
        }
    ]


@pytest.mark.anyio
async def test_agent_preset_sync_replaces_existing_normalized_subagent_edges(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    initial_files = _subagent_edge_git_tree(
        subagents=[{"slug": "old-child"}],
        child_slugs=["old-child", "new-child"],
    )
    initial_snapshot, diagnostics = await service.parse_files(
        initial_files,
        commit_sha="k" * 40,
    )
    assert diagnostics == []
    importer = WorkspaceResourceImportService(session=session, role=svc_role)
    await importer.import_non_workflow_resources(initial_snapshot.spec)

    parent = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "edge-parent",
        )
    )
    assert parent is not None
    original_version = await session.scalar(
        select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == svc_role.workspace_id,
            AgentPresetVersion.preset_id == parent.id,
            AgentPresetVersion.version == 1,
        )
    )
    assert original_version is not None
    assert (
        await session.scalar(
            select(AgentPresetVersionSubagent.child_preset_id).where(
                AgentPresetVersionSubagent.workspace_id == svc_role.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id
                == original_version.id,
            )
        )
        is not None
    )

    updated_files = _subagent_edge_git_tree(
        subagents=[
            {
                "slug": "new-child",
                "name": "replacement-child",
                "description": "Use the replacement child",
                "max_turns": 4,
            }
        ],
        child_slugs=["old-child", "new-child"],
    )
    updated_snapshot, diagnostics = await service.parse_files(
        updated_files,
        commit_sha="l" * 40,
    )
    assert diagnostics == []
    await importer.import_non_workflow_resources(updated_snapshot.spec)

    updated_version = await session.scalar(
        select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == svc_role.workspace_id,
            AgentPresetVersion.preset_id == parent.id,
            AgentPresetVersion.version == 1,
        )
    )
    new_child = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "new-child",
        )
    )
    assert updated_version is not None
    assert new_child is not None
    assert updated_version.id == original_version.id
    assert updated_version.subagents_enabled is True
    edges = (
        await session.scalars(
            select(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.workspace_id == svc_role.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id
                == updated_version.id,
            )
        )
    ).all()
    assert len(edges) == 1
    edge = edges[0]
    assert edge.child_preset_id == new_child.id
    assert edge.alias == "replacement-child"
    assert edge.description == "Use the replacement child"
    assert edge.max_turns == 4
    head_edges = (
        await session.scalars(
            select(AgentPresetSubagent).where(
                AgentPresetSubagent.workspace_id == svc_role.workspace_id,
                AgentPresetSubagent.parent_preset_id == parent.id,
            )
        )
    ).all()
    assert len(head_edges) == 1
    head_edge = head_edges[0]
    assert head_edge.child_preset_id == new_child.id

    runtime_binding = await AgentPresetService(
        session=session,
        role=svc_role,
    ).get_version_subagent_binding(updated_version)
    assert runtime_binding.enabled is True
    assert [subagent.preset_id for subagent in runtime_binding.subagents] == [
        new_child.id
    ]


@pytest.mark.anyio
async def test_agent_preset_sync_clears_normalized_subagent_edges(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    initial_snapshot, diagnostics = await service.parse_files(
        _subagent_edge_git_tree(
            subagents=[{"slug": "old-child"}],
            child_slugs=["old-child"],
        ),
        commit_sha="m" * 40,
    )
    assert diagnostics == []
    importer = WorkspaceResourceImportService(session=session, role=svc_role)
    await importer.import_non_workflow_resources(initial_snapshot.spec)

    cleared_snapshot, diagnostics = await service.parse_files(
        _subagent_edge_git_tree(subagents=[], child_slugs=["old-child"]),
        commit_sha="n" * 40,
    )
    assert diagnostics == []
    await importer.import_non_workflow_resources(cleared_snapshot.spec)

    parent = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "edge-parent",
        )
    )
    assert parent is not None
    version = await session.scalar(
        select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == svc_role.workspace_id,
            AgentPresetVersion.preset_id == parent.id,
            AgentPresetVersion.version == 1,
        )
    )
    assert version is not None
    assert parent.subagents_enabled is False
    assert version.subagents_enabled is False
    # The JSON stays the expand-window compatibility contract for old pods,
    # so clearing must round-trip through it as well as the edge tables.
    assert parent.agents == {"enabled": False, "subagents": []}
    assert version.agents == {"enabled": False, "subagents": []}
    assert (
        await session.scalars(
            select(AgentPresetSubagent).where(
                AgentPresetSubagent.workspace_id == svc_role.workspace_id,
                AgentPresetSubagent.parent_preset_id == parent.id,
            )
        )
    ).all() == []
    assert (
        await session.scalars(
            select(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.workspace_id == svc_role.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id == version.id,
            )
        )
    ).all() == []

    runtime_binding = await AgentPresetService(
        session=session,
        role=svc_role,
    ).get_version_subagent_binding(version)
    assert runtime_binding.enabled is False
    assert runtime_binding.subagents == []


@pytest.mark.anyio
async def test_agent_preset_import_creates_new_version_without_mutating_history(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    transport = AsyncMock()
    transport.read_files.side_effect = [
        VcsTreeSnapshot(
            commit_sha="i" * 40,
            tree_sha="tree-1",
            files=_agent_preset_git_tree(
                source_id="qa-versioned",
                slug="qa-versioned",
                name="QA versioned",
                instructions="Original instructions",
            ),
        ),
        VcsTreeSnapshot(
            commit_sha="j" * 40,
            tree_sha="tree-2",
            files=_agent_preset_git_tree(
                source_id="qa-versioned",
                slug="qa-versioned",
                name="QA versioned",
                instructions="Updated instructions",
                version_number=2,
            ),
        ),
    ]
    service._workspace_git_url = AsyncMock(
        return_value=GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    )

    with patch(
        "tracecat.workspace_sync.service.vcs_transport_for_provider",
        return_value=transport,
    ):
        first_result = await service.pull(options=PullOptions(commit_sha="i" * 40))
        second_result = await service.pull(options=PullOptions(commit_sha="j" * 40))

    assert first_result.success is True
    assert second_result.success is True
    preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "qa-versioned",
        )
    )
    assert preset is not None
    versions = list(
        (
            await session.scalars(
                select(AgentPresetVersion)
                .where(
                    AgentPresetVersion.workspace_id == svc_role.workspace_id,
                    AgentPresetVersion.preset_id == preset.id,
                )
                .order_by(AgentPresetVersion.version.asc())
            )
        ).all()
    )
    assert [version.instructions for version in versions] == [
        "Original instructions",
        "Updated instructions",
    ]
    assert preset.current_version_id == versions[-1].id


@pytest.mark.anyio
async def test_agent_preset_sync_preserves_catalog_id(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    catalog = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=svc_role.organization_id,
        model_provider="openai",
        model_name="gpt-5.5",
    )
    session.add(catalog)
    await session.flush()
    files = _agent_preset_git_tree(
        source_id="qa-catalog-backed",
        slug="qa-catalog-backed",
        name="QA catalog backed",
    )
    preset_path = f"{AGENT_PRESET_ROOT}/qa-catalog-backed/versions/1.yml"
    preset_spec = yaml.safe_load(files[preset_path])
    preset_spec["catalog_id"] = str(catalog.id)
    files[preset_path] = _yaml(preset_spec)

    snapshot, diagnostics = await service.parse_files(files, commit_sha="l" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == svc_role.workspace_id,
            AgentPreset.slug == "qa-catalog-backed",
        )
    )
    assert preset is not None
    assert preset.catalog_id == catalog.id
    version = await session.scalar(
        select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == svc_role.workspace_id,
            AgentPresetVersion.preset_id == preset.id,
        )
    )
    assert version is not None
    assert version.catalog_id == catalog.id

    projection = await service.project_workspace(create_missing_mappings=False)
    projected_spec = yaml.safe_load(projection.files[preset_path])
    assert projected_spec["catalog_id"] == str(catalog.id)


@pytest.mark.anyio
async def test_case_tag_import_allows_in_batch_name_swap(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    tag_a = CaseTag(
        workspace_id=svc_role.workspace_id,
        ref="tag-a",
        name="Alpha",
        color="#101010",
    )
    tag_b = CaseTag(
        workspace_id=svc_role.workspace_id,
        ref="tag-b",
        name="Beta",
        color="#202020",
    )
    session.add_all([tag_a, tag_b])
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_TAG_ROOT}/tag-a.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "tag-a",
                "name": "Beta",
                "color": "#303030",
            }
        ),
        f"{CASE_TAG_ROOT}/tag-b.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "tag-b",
                "name": "Alpha",
                "color": "#404040",
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="m" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(tag_a)
    await session.refresh(tag_b)
    assert tag_a.name == "Beta"
    assert tag_a.color == "#303030"
    assert tag_b.name == "Alpha"
    assert tag_b.color == "#404040"


@pytest.mark.anyio
async def test_case_tag_import_allows_mapped_ref_swap(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    tag_a = CaseTag(
        workspace_id=svc_role.workspace_id,
        ref="tag-b",
        name="Alpha",
        color="#101010",
    )
    tag_b = CaseTag(
        workspace_id=svc_role.workspace_id,
        ref="tag-a",
        name="Beta",
        color="#202020",
    )
    session.add_all([tag_a, tag_b])
    await session.flush()
    session.add_all(
        [
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.CASE_TAG.value,
                source_id="tag-a",
                source_path=f"{CASE_TAG_ROOT}/tag-a.yml",
                local_id=tag_a.id,
            ),
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.CASE_TAG.value,
                source_id="tag-b",
                source_path=f"{CASE_TAG_ROOT}/tag-b.yml",
                local_id=tag_b.id,
            ),
        ]
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_TAG_ROOT}/tag-a.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "tag-a",
                "name": "Alpha",
                "color": "#303030",
            }
        ),
        f"{CASE_TAG_ROOT}/tag-b.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "tag-b",
                "name": "Beta",
                "color": "#404040",
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="r" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(tag_a)
    await session.refresh(tag_b)
    assert tag_a.ref == "tag-a"
    assert tag_a.color == "#303030"
    assert tag_b.ref == "tag-b"
    assert tag_b.color == "#404040"


@pytest.mark.anyio
async def test_case_tag_import_reuses_source_id_mapping_after_local_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    tag = CaseTag(
        workspace_id=svc_role.workspace_id,
        name="Renamed Alert",
        ref="renamed-alert",
        color="#202020",
    )
    session.add(tag)
    await session.flush()
    session.add(
        WorkspaceSyncResourceMapping(
            workspace_id=svc_role.workspace_id,
            provider=VcsProvider.GITHUB.value,
            resource_type=SyncResourceType.CASE_TAG.value,
            source_id="qa-alert",
            source_path=f"{CASE_TAG_ROOT}/qa-alert.yml",
            local_id=tag.id,
        )
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_TAG_ROOT}/qa-alert.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "qa-alert",
                "name": "Original Alert",
                "color": "#303030",
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="c" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(tag)
    tags = list(
        (
            await session.scalars(
                select(CaseTag).where(CaseTag.workspace_id == svc_role.workspace_id)
            )
        ).all()
    )
    assert len(tags) == 1
    assert tag.name == "Original Alert"
    assert tag.ref == "qa-alert"
    assert tag.color == "#303030"


@pytest.mark.anyio
async def test_workflow_case_trigger_tag_filters_project_case_tag_source_ids(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    tag = CaseTag(
        workspace_id=svc_role.workspace_id,
        name="Renamed Alert",
        ref="renamed-alert",
        color="#202020",
    )
    session.add(tag)
    await session.flush()
    session.add(
        WorkspaceSyncResourceMapping(
            workspace_id=svc_role.workspace_id,
            provider=VcsProvider.GITHUB.value,
            resource_type=SyncResourceType.CASE_TAG.value,
            source_id="qa-alert",
            source_path=f"{CASE_TAG_ROOT}/qa-alert.yml",
            local_id=tag.id,
        )
    )
    await session.flush()
    spec = WorkflowResourceSpec.model_validate(
        _workflow_spec(
            source_id="qa-workflow",
            title="qa-workflow",
            alias="qa-workflow",
            folder_path="QA/Workflows",
            actions=[
                {
                    "ref": "reshape",
                    "action": "core.transform.reshape",
                    "args": {"value": "${{ TRIGGER.value }}"},
                }
            ],
            case_trigger=True,
        )
    )
    assert spec.case_trigger is not None
    local_ref_spec = spec.model_copy(
        update={
            "case_trigger": spec.case_trigger.model_copy(
                update={"tag_filters": ["renamed-alert"]}
            )
        }
    )

    rewritten = await WorkspaceSyncService(
        session=session,
        role=svc_role,
    )._workflow_specs_with_case_tag_source_ids({"qa-workflow": local_ref_spec})

    rewritten_trigger = rewritten["qa-workflow"].case_trigger
    assert rewritten_trigger is not None
    assert rewritten_trigger.tag_filters == ["qa-alert"]


@pytest.mark.anyio
async def test_case_dropdown_import_reuses_source_id_mapping_after_local_rename(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    dropdown = CaseDropdownDefinition(
        workspace_id=svc_role.workspace_id,
        name="Renamed Reason",
        ref="renamed_reason",
        is_ordered=False,
        icon_name=None,
        position=7,
        required_on_closure=False,
    )
    session.add(dropdown)
    await session.flush()
    session.add_all(
        [
            CaseDropdownOption(
                definition_id=dropdown.id,
                ref="old",
                label="Old",
                position=0,
            ),
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.CASE_DROPDOWN.value,
                source_id="qa_resolution_reason",
                source_path=f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml",
                local_id=dropdown.id,
            ),
        ]
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml": _yaml(
            {
                "version": 1,
                "type": "case_dropdown",
                "id": "qa_resolution_reason",
                "name": "Resolution Reason",
                "is_ordered": True,
                "icon_name": "shield-alert",
                "position": 2,
                "required_on_closure": True,
                "options": [
                    {
                        "ref": "false_positive",
                        "label": "False positive",
                        "position": 0,
                        "color": "#303030",
                    }
                ],
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="d" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(dropdown, ["options"])
    definitions = list(
        (
            await session.scalars(
                select(CaseDropdownDefinition).where(
                    CaseDropdownDefinition.workspace_id == svc_role.workspace_id
                )
            )
        ).all()
    )
    assert len(definitions) == 1
    assert dropdown.name == "Resolution Reason"
    assert dropdown.ref == "qa_resolution_reason"
    assert dropdown.is_ordered is True
    assert dropdown.icon_name == "shield-alert"
    assert dropdown.position == 2
    assert dropdown.required_on_closure is True
    assert [(option.ref, option.label) for option in dropdown.options] == [
        ("false_positive", "False positive")
    ]


@pytest.mark.anyio
async def test_variable_import_allows_in_batch_name_swap(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    variable_a = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="Alpha",
        environment="default",
        values={"value": "a"},
    )
    variable_b = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="Beta",
        environment="default",
        values={"value": "b"},
    )
    session.add_all([variable_a, variable_b])
    await session.flush()
    session.add_all(
        [
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.VARIABLE.value,
                source_id="default/alpha",
                source_path=f"{VARIABLE_ROOT}/default/alpha.yml",
                local_id=variable_a.id,
            ),
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.VARIABLE.value,
                source_id="default/beta",
                source_path=f"{VARIABLE_ROOT}/default/beta.yml",
                local_id=variable_b.id,
            ),
        ]
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{VARIABLE_ROOT}/default/alpha.yml": _yaml(
            {
                "version": 1,
                "type": "variable",
                "id": "default/alpha",
                "name": "Beta",
                "environment": "default",
                "keys": ["value"],
            }
        ),
        f"{VARIABLE_ROOT}/default/beta.yml": _yaml(
            {
                "version": 1,
                "type": "variable",
                "id": "default/beta",
                "name": "Alpha",
                "environment": "default",
                "keys": ["value"],
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="w" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(variable_a)
    await session.refresh(variable_b)
    assert variable_a.name == "Beta"
    assert variable_a.values == {"value": "a"}
    assert variable_b.name == "Alpha"
    assert variable_b.values == {"value": "b"}


@pytest.mark.anyio
async def test_secret_metadata_import_allows_in_batch_name_swap(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    secret_service = SecretsService(session=session, role=svc_role)
    secret_a = Secret(
        workspace_id=svc_role.workspace_id,
        name="Alpha",
        environment="default",
        encrypted_keys=secret_service.encrypt_keys(
            [SecretKeyValue(key="TOKEN", value=SecretStr("a"))]
        ),
    )
    secret_b = Secret(
        workspace_id=svc_role.workspace_id,
        name="Beta",
        environment="default",
        encrypted_keys=secret_service.encrypt_keys(
            [SecretKeyValue(key="TOKEN", value=SecretStr("b"))]
        ),
    )
    session.add_all([secret_a, secret_b])
    await session.flush()
    session.add_all(
        [
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.SECRET_METADATA.value,
                source_id="default/alpha",
                source_path=f"{SECRET_METADATA_ROOT}/default/alpha.yml",
                local_id=secret_a.id,
            ),
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.SECRET_METADATA.value,
                source_id="default/beta",
                source_path=f"{SECRET_METADATA_ROOT}/default/beta.yml",
                local_id=secret_b.id,
            ),
        ]
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{SECRET_METADATA_ROOT}/default/alpha.yml": _yaml(
            {
                "version": 1,
                "type": "secret_metadata",
                "id": "default/alpha",
                "name": "Beta",
                "environment": "default",
                "keys": ["TOKEN"],
            }
        ),
        f"{SECRET_METADATA_ROOT}/default/beta.yml": _yaml(
            {
                "version": 1,
                "type": "secret_metadata",
                "id": "default/beta",
                "name": "Alpha",
                "environment": "default",
                "keys": ["TOKEN"],
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="s" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(secret_a)
    await session.refresh(secret_b)
    assert secret_a.name == "Beta"
    assert secret_b.name == "Alpha"
    decrypted_a = {
        key_value.key: key_value.value.get_secret_value()
        for key_value in secret_service.decrypt_keys(secret_a.encrypted_keys)
    }
    decrypted_b = {
        key_value.key: key_value.value.get_secret_value()
        for key_value in secret_service.decrypt_keys(secret_b.encrypted_keys)
    }
    assert decrypted_a == {"TOKEN": "a"}
    assert decrypted_b == {"TOKEN": "b"}


@pytest.mark.anyio
async def test_variable_import_temporary_name_avoids_existing_variables(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    variable = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name="Alpha",
        environment="default",
        values={"value": "a"},
    )
    session.add(variable)
    await session.flush()
    colliding_name = f"__tracecat_sync_tmp_{variable.id.hex}"
    unrelated = WorkspaceVariable(
        workspace_id=svc_role.workspace_id,
        name=colliding_name,
        environment="default",
        values={"value": "unrelated"},
    )
    session.add_all(
        [
            unrelated,
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=VcsProvider.GITHUB.value,
                resource_type=SyncResourceType.VARIABLE.value,
                source_id="default/alpha",
                source_path=f"{VARIABLE_ROOT}/default/alpha.yml",
                local_id=variable.id,
            ),
        ]
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{VARIABLE_ROOT}/default/alpha.yml": _yaml(
            {
                "version": 1,
                "type": "variable",
                "id": "default/alpha",
                "name": "Gamma",
                "environment": "default",
                "keys": ["value"],
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="z" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    await session.refresh(variable)
    await session.refresh(unrelated)
    assert variable.name == "Gamma"
    assert variable.values == {"value": "a"}
    assert unrelated.name == colliding_name


@pytest.mark.anyio
async def test_case_field_sync_preserves_select_options(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_FIELD_ROOT}/severity_band.yml": _yaml(
            {
                "version": 1,
                "type": "case_field",
                "id": "severity_band",
                "name": "severity_band",
                "field_type": "select",
                "options": ["low", "medium", "high"],
                "required_on_closure": True,
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="n" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)
    definition = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == svc_role.workspace_id)
    )
    assert definition is not None
    assert definition.schema["severity_band"]["options"] == ["low", "medium", "high"]
    assert definition.schema["severity_band"]["required_on_closure"] is True

    projection = await service.project_workspace(create_missing_mappings=False)
    field_spec = yaml.safe_load(
        projection.files[f"{CASE_FIELD_ROOT}/severity_band.yml"]
    )
    assert field_spec["options"] == ["low", "medium", "high"]
    assert field_spec["required_on_closure"] is True


@pytest.mark.anyio
async def test_project_workspace_maps_case_fields_per_field(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    session.add(
        CaseFields(
            workspace_id=svc_role.workspace_id,
            schema={
                "external_ref": {"type": "TEXT"},
                "severity_band": {
                    "type": "SELECT",
                    "options": ["low", "medium", "high"],
                },
            },
        )
    )
    await session.flush()
    service = WorkspaceSyncService(session=session, role=svc_role)

    projection = await service.project_workspace()

    assert f"{CASE_FIELD_ROOT}/external_ref.yml" in projection.files
    assert f"{CASE_FIELD_ROOT}/severity_band.yml" in projection.files
    mappings = list(
        (
            await session.scalars(
                select(WorkspaceSyncResourceMapping).where(
                    WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id,
                    WorkspaceSyncResourceMapping.resource_type
                    == SyncResourceType.CASE_FIELD.value,
                )
            )
        ).all()
    )
    assert {mapping.source_id for mapping in mappings} == {
        "external_ref",
        "severity_band",
    }
    assert len({mapping.local_id for mapping in mappings}) == 2


@pytest.mark.anyio
async def test_case_duration_sync_preserves_anchor_fields(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Case duration anchors round-trip their timestamp and field-filter config.

    The shared full-tree fixture only sets ``event``/``selection`` on each
    anchor, so ``timestamp_path`` and ``field_filters`` are otherwise never
    exercised. This pins both anchors' non-default config through import into the
    database and back out through projection.
    """
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml": _yaml(
            {
                "version": 1,
                "type": "case_duration",
                "id": "qa_time_to_triage",
                "name": "qa_time_to_triage",
                "description": "Time from case open to triage",
                "start": {
                    "event": "case_created",
                    "selection": "last",
                    "timestamp_path": "opened_at",
                    "field_filters": {"source": "alert"},
                },
                "end": {
                    "event": "status_changed",
                    "selection": "first",
                    "timestamp_path": "resolved_at",
                    "field_filters": {"status": "triaged"},
                },
            }
        ),
    }

    snapshot, diagnostics = await service.parse_files(files, commit_sha="u" * 40)

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    duration = await session.scalar(
        select(CaseDurationDefinition).where(
            CaseDurationDefinition.workspace_id == svc_role.workspace_id,
            CaseDurationDefinition.name == "qa_time_to_triage",
        )
    )
    assert duration is not None
    assert duration.start_event_type == "case_created"
    assert duration.start_selection == "last"
    assert duration.start_timestamp_path == "opened_at"
    assert duration.start_field_filters == {"source": "alert"}
    assert duration.end_event_type == "status_changed"
    assert duration.end_selection == "first"
    assert duration.end_timestamp_path == "resolved_at"
    assert duration.end_field_filters == {"status": "triaged"}

    projection = await service.project_workspace(create_missing_mappings=False)
    duration_spec = yaml.safe_load(
        projection.files[f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml"]
    )
    assert duration_spec["start"] == {
        "event": "case_created",
        "selection": "last",
        "timestamp_path": "opened_at",
        "field_filters": {"source": "alert"},
    }
    assert duration_spec["end"] == {
        "event": "status_changed",
        "selection": "first",
        "timestamp_path": "resolved_at",
        "field_filters": {"status": "triaged"},
    }


@pytest.mark.anyio
async def test_table_import_rejects_multiple_unique_columns(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/qa_multi_unique/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_multi_unique",
                "name": "qa_multi_unique",
                "columns": [
                    {"name": "indicator", "type": "text", "unique": True},
                    {"name": "source", "type": "text", "unique": True},
                ],
            }
        ),
    }
    snapshot, diagnostics = await service.parse_files(files, commit_sha="k" * 40)

    assert diagnostics == []
    with pytest.raises(ValueError, match="at most one unique column"):
        await WorkspaceResourceImportService(
            session=session,
            role=svc_role,
        ).import_non_workflow_resources(snapshot.spec)


@pytest.mark.anyio
async def test_table_specs_reject_runtime_row_fields(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/qa_non_unique/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_non_unique",
                "name": "qa_non_unique",
                "columns": [{"name": "indicator", "type": "text"}],
                "rows_path": "rows.jsonl",
            }
        ),
        f"{TABLE_ROOT}/qa_non_unique/rows.jsonl": "\n".join(
            [
                json.dumps({"indicator": "alpha"}, sort_keys=True),
                json.dumps({"indicator": "beta"}, sort_keys=True),
            ]
        )
        + "\n",
    }
    snapshot, diagnostics = await service.parse_files(files, commit_sha="v" * 40)

    assert snapshot.spec.tables == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].workflow_path == f"{TABLE_ROOT}/qa_non_unique/table.yml"
    assert diagnostics[0].error_type == "validation"
    assert "rows_path" in diagnostics[0].message


@pytest.mark.anyio
async def test_table_import_updates_existing_column_metadata(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    first_files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/qa_column_updates/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_column_updates",
                "name": "qa_column_updates",
                "columns": [
                    {"name": "indicator", "type": "text", "unique": True},
                    {"name": "status", "type": "text"},
                ],
            }
        ),
    }
    second_files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/qa_column_updates/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_column_updates",
                "name": "qa_column_updates",
                "columns": [
                    {"name": "indicator", "type": "text"},
                    {
                        "name": "status",
                        "type": "select",
                        "nullable": False,
                        "default": "medium",
                        "options": ["low", "medium", "high"],
                        "unique": True,
                    },
                ],
            }
        ),
    }

    first_snapshot, first_diagnostics = await service.parse_files(
        first_files,
        commit_sha="x" * 40,
    )
    second_snapshot, second_diagnostics = await service.parse_files(
        second_files,
        commit_sha="y" * 40,
    )

    assert first_diagnostics == []
    assert second_diagnostics == []
    importer = WorkspaceResourceImportService(session=session, role=svc_role)
    await importer.import_non_workflow_resources(first_snapshot.spec)
    await importer.import_non_workflow_resources(second_snapshot.spec)

    table_service = BaseTablesService(session=session, role=svc_role)
    table = await table_service.get_table_by_name("qa_column_updates")
    await session.refresh(table, ["columns"])
    columns = {column.name: column for column in table.columns}
    status = columns["status"]
    assert status.type == "SELECT"
    assert status.nullable is False
    assert status.default == "medium"
    assert status.options == ["low", "medium", "high"]
    assert set(await table_service.get_index(table)) == {"status"}


@pytest.mark.anyio
async def test_table_import_rejects_columns_removed_from_git_spec(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceSyncService(session=session, role=svc_role)
    first_files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/qa_column_removals/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_column_removals",
                "name": "qa_column_removals",
                "columns": [
                    {"name": "indicator", "type": "text"},
                    {"name": "status", "type": "text"},
                ],
            }
        ),
    }
    second_files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/qa_column_removals/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_column_removals",
                "name": "qa_column_removals",
                "columns": [{"name": "indicator", "type": "text"}],
            }
        ),
    }

    first_snapshot, first_diagnostics = await service.parse_files(
        first_files,
        commit_sha="a" * 40,
    )
    second_snapshot, second_diagnostics = await service.parse_files(
        second_files,
        commit_sha="b" * 40,
    )

    assert first_diagnostics == []
    assert second_diagnostics == []
    importer = WorkspaceResourceImportService(session=session, role=svc_role)
    await importer.import_non_workflow_resources(first_snapshot.spec)
    with pytest.raises(ValueError, match="status"):
        await importer.import_non_workflow_resources(second_snapshot.spec)

    table = await BaseTablesService(session=session, role=svc_role).get_table_by_name(
        "qa_column_removals"
    )
    await session.refresh(table, ["columns"])
    assert {column.name for column in table.columns} == {"indicator", "status"}


async def _set_workspace_git_repo_url(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_url: str,
    provider: VcsProvider = VcsProvider.GITHUB,
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "git_provider": provider,
        "git_repo_url": repo_url,
    }
    session.add(workspace)
    await session.flush()


async def _create_workspace_role(
    session: AsyncSession,
    *,
    source_role: Role,
    workspace_name: str,
    repo_url: str,
    provider: VcsProvider = VcsProvider.GITHUB,
) -> Role:
    assert source_role.organization_id is not None
    workspace = Workspace(
        name=workspace_name,
        organization_id=source_role.organization_id,
        settings={"git_provider": provider, "git_repo_url": repo_url},
    )
    session.add(workspace)
    await session.flush()
    return source_role.model_copy(update={"workspace_id": workspace.id})


async def _legacy_upgrade_harness(
    session: AsyncSession,
    *,
    source_role: Role,
    repo_name: str,
    target_workspace_name: str,
    legacy_files: dict[str, str],
) -> LegacyUpgradeHarness:
    """Create source/target services that share a seeded legacy fake repo."""
    assert source_role.workspace_id is not None
    repo_url = f"git+ssh://git@github.com/TracecatHQ/{repo_name}.git"
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo=repo_name)
    fake_vcs = FakeVcsServer()
    await _set_workspace_git_repo_url(
        session,
        workspace_id=source_role.workspace_id,
        repo_url=repo_url,
    )
    target_role = await _create_workspace_role(
        session,
        source_role=source_role,
        workspace_name=target_workspace_name,
        repo_url=repo_url,
    )
    source_service = WorkspaceSyncService(
        session=session,
        role=source_role,
        transport_factory=fake_vcs.transport_factory,
    )
    target_service = WorkspaceSyncService(
        session=session,
        role=target_role,
        transport_factory=fake_vcs.transport_factory,
    )
    seed_transport = fake_vcs.transport_factory(
        VcsProvider.GITHUB,
        session=session,
        role=source_role,
    )
    legacy_commit = await seed_transport.write_files(
        url=git_url,
        files=legacy_files,
        message="Seed legacy workflow sync repository",
        branch="main",
        create_pr=False,
    )
    assert legacy_commit.sha is not None
    return LegacyUpgradeHarness(
        git_url=git_url,
        fake_vcs=fake_vcs,
        source_service=source_service,
        target_service=target_service,
        target_role=target_role,
        legacy_commit_sha=legacy_commit.sha,
    )


async def _import_expanded_non_workflow_resources(
    session: AsyncSession,
    *,
    role: Role,
    service: WorkspaceSyncService,
) -> None:
    """Write the expanded non-workflow fixture into one workspace's DB."""
    snapshot, diagnostics = await service.parse_files(
        _expanded_full_git_tree(include_schedules=False),
        commit_sha="e" * 40,
    )
    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=role,
    ).import_non_workflow_resources(snapshot.spec)


async def _refresh_workspace_for_fixture_cleanup(
    session: AsyncSession,
    *,
    role: Role,
) -> None:
    """Re-load the workspace row so post-rollback fixture teardown is safe.

    A rolled-back import expires the ORM instances in the shared test session.
    Refreshing the workspace eagerly here keeps pytest's fixture cleanup from
    triggering lazy IO outside SQLAlchemy's async greenlet context, which would
    otherwise raise ``MissingGreenlet`` during teardown.
    """
    workspace_id = role.workspace_id
    assert workspace_id is not None
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    assert workspace is not None
    await session.refresh(workspace)


async def _assert_projected_workspaces_match(
    source_service: WorkspaceSyncService,
    target_service: WorkspaceSyncService,
) -> None:
    """Compare canonical sync files, not DB IDs or other local-only state.

    Each service projects under the provider it was constructed with.
    """
    source_projection = await source_service.project_workspace(
        create_missing_mappings=False
    )
    target_projection = await target_service.project_workspace(
        create_missing_mappings=False
    )
    assert target_projection.files == source_projection.files


async def _mapping_for(
    session: AsyncSession,
    *,
    role: Role,
    resource_type: SyncResourceType,
    source_id: str,
    provider: VcsProvider = VcsProvider.GITHUB,
) -> WorkspaceSyncResourceMapping | None:
    """Look up a workspace's sync mapping for one Git source identifier."""
    workspace_id = role.workspace_id
    assert workspace_id is not None
    return await session.scalar(
        select(WorkspaceSyncResourceMapping).where(
            WorkspaceSyncResourceMapping.workspace_id == workspace_id,
            WorkspaceSyncResourceMapping.provider == provider.value,
            WorkspaceSyncResourceMapping.resource_type == resource_type.value,
            WorkspaceSyncResourceMapping.source_id == source_id,
        )
    )


async def _assert_mapping_targets(
    session: AsyncSession,
    *,
    role: Role,
    resource_type: SyncResourceType,
    source_id: str,
    local_id: uuid.UUID,
    provider: VcsProvider = VcsProvider.GITHUB,
) -> None:
    """Assert a sync mapping exists and points at the expected local row."""
    mapping = await _mapping_for(
        session,
        role=role,
        resource_type=resource_type,
        source_id=source_id,
        provider=provider,
    )
    assert mapping is not None
    assert mapping.local_id == local_id


async def _assert_workspace_has_expanded_resource_rows(
    session: AsyncSession,
    role: Role,
) -> None:
    """Assert the upgraded destination contains representative new resources.

    File-level parity (``_assert_projected_workspaces_match``) only proves the
    bytes round-tripped. This helper queries the DB directly to prove the pull
    actually *materialised* one row from every new resource family, so a silent
    "files written but not imported" regression can't pass.
    """
    workspace_id = role.workspace_id
    assert workspace_id is not None
    # Workflow: the legacy resource family must survive the upgrade, keyed by the
    # alias it was seeded with.
    assert await session.scalar(
        select(Workflow).where(
            Workflow.workspace_id == workspace_id,
            Workflow.alias == "qa-root",
        )
    )
    # Agent preset + its skill: presets are folder-based resources, skills are
    # nested files under them, so both confirm the nested layout imported.
    assert await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert await session.scalar(
        select(Skill).where(
            Skill.workspace_id == workspace_id,
            Skill.name == "qa-enrichment-skill",
        )
    )
    # Table: schema-bearing resource.
    assert await session.scalar(
        select(Table).where(
            Table.workspace_id == workspace_id,
            Table.name == "qa_indicators",
        )
    )
    # Case tag: simple single-file case resource keyed by ref.
    assert await session.scalar(
        select(CaseTag).where(
            CaseTag.workspace_id == workspace_id,
            CaseTag.ref == "qa-alert",
        )
    )
    # Case dropdown + duration definitions: case configuration resources.
    assert await session.scalar(
        select(CaseDropdownDefinition).where(
            CaseDropdownDefinition.workspace_id == workspace_id,
            CaseDropdownDefinition.ref == "qa_resolution_reason",
        )
    )
    assert await session.scalar(
        select(CaseDurationDefinition).where(
            CaseDurationDefinition.workspace_id == workspace_id,
            CaseDurationDefinition.name == "qa_time_to_triage",
        )
    )
    # Case fields: a single per-workspace row whose JSON schema must include the
    # custom field, so check the column was merged in rather than just present.
    case_fields = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == workspace_id)
    )
    assert case_fields is not None
    assert "qa_external_ref" in (case_fields.schema or {})
    # Variable + secret: environment-scoped resources, so the env discriminator is
    # part of the lookup to prove it round-tripped too.
    assert await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == workspace_id,
            WorkspaceVariable.name == "qa_config",
            WorkspaceVariable.environment == "default",
        )
    )
    assert await session.scalar(
        select(Secret).where(
            Secret.workspace_id == workspace_id,
            Secret.name == "qa_threatintel",
            Secret.environment == "default",
        )
    )


async def _assert_workspace_has_no_expanded_resource_rows(
    session: AsyncSession,
    *,
    role: Role,
) -> None:
    """Assert a failed upgrade did not leave representative new resource rows."""
    workspace_id = role.workspace_id
    assert workspace_id is not None
    assert (
        await session.scalar(
            select(AgentPreset).where(
                AgentPreset.workspace_id == workspace_id,
                AgentPreset.slug == "qa-triage-parent",
            )
        )
    ) is None
    assert (
        await session.scalar(
            select(Skill).where(
                Skill.workspace_id == workspace_id,
                Skill.name == "qa-enrichment-skill",
            )
        )
    ) is None
    assert (
        await session.scalar(
            select(Table).where(
                Table.workspace_id == workspace_id,
                Table.name == "qa_indicators",
            )
        )
    ) is None
    assert (
        await session.scalar(
            select(CaseTag).where(
                CaseTag.workspace_id == workspace_id,
                CaseTag.ref == "qa-alert",
            )
        )
    ) is None
    assert (
        await session.scalar(
            select(CaseDropdownDefinition).where(
                CaseDropdownDefinition.workspace_id == workspace_id,
                CaseDropdownDefinition.ref == "qa_resolution_reason",
            )
        )
    ) is None
    assert (
        await session.scalar(
            select(CaseDurationDefinition).where(
                CaseDurationDefinition.workspace_id == workspace_id,
                CaseDurationDefinition.name == "qa_time_to_triage",
            )
        )
    ) is None
    case_fields = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == workspace_id)
    )
    if case_fields is not None:
        assert "qa_external_ref" not in (case_fields.schema or {})
    assert (
        await session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == workspace_id,
                WorkspaceVariable.name == "qa_config",
                WorkspaceVariable.environment == "default",
            )
        )
    ) is None
    assert (
        await session.scalar(
            select(Secret).where(
                Secret.workspace_id == workspace_id,
                Secret.name == "qa_threatintel",
                Secret.environment == "default",
            )
        )
    ) is None


async def _mutate_source_workspace_for_roundtrip_update(
    session: AsyncSession,
    *,
    role: Role,
) -> None:
    assert role.workspace_id is not None
    table = await session.scalar(
        select(Table).where(
            Table.workspace_id == role.workspace_id,
            Table.name == "qa_indicators",
        )
    )
    assert table is not None
    await BaseTablesService(session=session, role=role).update_table(
        table,
        TableUpdate(name="qa_indicators_roundtrip"),
    )

    tag = await session.scalar(
        select(CaseTag).where(
            CaseTag.workspace_id == role.workspace_id,
            CaseTag.ref == "qa-alert",
        )
    )
    assert tag is not None
    tag.name = "QA alert roundtrip"

    variable = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == role.workspace_id,
            WorkspaceVariable.name == "qa_config",
            WorkspaceVariable.environment == "default",
        )
    )
    assert variable is not None
    variable.name = "qa_config_roundtrip"
    variable.description = "QA config variable updated by roundtrip"

    session.add(
        CaseTag(
            workspace_id=role.workspace_id,
            ref="roundtrip-followup",
            name="Roundtrip followup",
            color="#444CE7",
        )
    )
    await session.flush()


def _legacy_workflow_only_git_tree() -> dict[str, str]:
    """Old sync repository layout: workflow YAML files only, no manifest.

    Models a parent (``qa-root``) that invokes a child (``qa-child``) by alias so
    the seed exercises cross-workflow references, the trickiest thing to preserve
    across an upgrade.
    """
    return {
        f"{WORKFLOW_ROOT}/qa-root/definition.yml": _legacy_workflow_yaml(
            _workflow_spec(
                source_id="qa-root",
                title="qa-root-orchestrator",
                alias="qa-root",
                folder_path="QA/Root",
                actions=[
                    {
                        "ref": "execute_child",
                        "action": "core.workflow.execute",
                        "args": {"workflow_alias": "qa-child"},
                    }
                ],
            )
        ),
        f"{WORKFLOW_ROOT}/qa-child/definition.yml": _legacy_workflow_yaml(
            _workflow_spec(
                source_id="qa-child",
                title="qa-child-enrichment",
                alias="qa-child",
                folder_path="QA/Children",
                actions=[
                    {
                        "ref": "enrich",
                        "action": "core.transform.reshape",
                        "args": {"value": "${{ TRIGGER.indicator }}"},
                    }
                ],
            )
        ),
    }


def _legacy_string_manifest_git_tree() -> dict[str, str]:
    """Old workflow-only layout with the historical string-version manifest."""
    return dict(
        sorted(
            {
                MANIFEST_FILENAME: '{"version":"1"}',
                **_legacy_workflow_only_git_tree(),
            }.items()
        )
    )


def _legacy_workflow_yaml(spec: dict[str, Any]) -> str:
    """Downgrade a modern workflow spec into the legacy on-disk shape.

    Legacy files predate the expanded format, so strip the fields it added
    (``version``/``type``) and rewrite ``id`` into the old ``wf_<hex>`` form
    instead of the prefixed-ULID used today. Copy first so the caller's spec is
    left untouched.
    """
    legacy = deepcopy(spec)
    legacy.pop("version", None)
    legacy.pop("type", None)
    legacy["id"] = "wf_" + "".join(char for char in spec["id"] if char.isalnum())
    return _yaml(legacy)


def _agent_preset_git_tree(
    *,
    source_id: str,
    slug: str,
    name: str,
    instructions: str | None = None,
    version_number: int = 1,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{AGENT_PRESET_ROOT}/{source_id}/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": source_id,
                "slug": slug,
                "name": name,
                "current_version": version_number,
            }
        ),
        f"{AGENT_PRESET_ROOT}/{source_id}/versions/{version_number}.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": version_number,
                "name": name,
                "instructions": instructions,
            }
        ),
    }


def _subagent_edge_git_tree(
    *,
    subagents: list[dict[str, Any]],
    child_slugs: list[str],
) -> dict[str, str]:
    """Build a preset tree for normalized subagent-edge sync tests."""
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{AGENT_PRESET_ROOT}/edge-parent/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "edge-parent",
                "slug": "edge-parent",
                "name": "Edge parent",
                "current_version": 1,
            }
        ),
        f"{AGENT_PRESET_ROOT}/edge-parent/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "Edge parent",
                "subagents": subagents,
            }
        ),
    }
    for child_slug in child_slugs:
        files[f"{AGENT_PRESET_ROOT}/{child_slug}/preset.yml"] = _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": child_slug,
                "slug": child_slug,
                "name": child_slug.replace("-", " ").title(),
                "current_version": 1,
            }
        )
        files[f"{AGENT_PRESET_ROOT}/{child_slug}/versions/1.yml"] = _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": child_slug.replace("-", " ").title(),
            }
        )
    return files


def _combined_git_tree(*trees: dict[str, str]) -> dict[str, str]:
    files = {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest())}
    for tree in trees:
        files.update(
            {
                path: content
                for path, content in tree.items()
                if path != MANIFEST_FILENAME
            }
        )
    return files


def _case_dropdown_git_tree(
    *,
    source_id: str,
    name: str,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_DROPDOWN_ROOT}/{source_id}.yml": _yaml(
            {
                "version": 1,
                "type": "case_dropdown",
                "id": source_id,
                "name": name,
                "options": [],
            }
        ),
    }


def _table_git_tree(
    *,
    source_id: str,
    name: str,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{TABLE_ROOT}/{source_id}/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": source_id,
                "name": name,
                "columns": [{"name": "indicator", "type": "text", "unique": True}],
            }
        ),
    }


def _case_duration_git_tree(
    *,
    source_id: str,
    name: str,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_DURATION_ROOT}/{source_id}.yml": _yaml(
            {
                "version": 1,
                "type": "case_duration",
                "id": source_id,
                "name": name,
                "start": {"event": "case_created", "selection": "first"},
                "end": {"event": "status_changed", "selection": "first"},
            }
        ),
    }


def _case_field_git_tree(
    *,
    source_id: str,
    name: str,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_FIELD_ROOT}/{source_id}.yml": _yaml(
            {
                "version": 1,
                "type": "case_field",
                "id": source_id,
                "name": name,
                "field_type": "text",
                "kind": "short_text",
            }
        ),
    }


def _skill_git_tree(
    *,
    source_id: str,
    slug: str,
    name: str,
) -> dict[str, str]:
    content = "# QA Enrichment Skill\n"
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{SKILL_ROOT}/{source_id}/skill.yml": _yaml(
            {
                "version": 1,
                "type": "skill",
                "id": source_id,
                "slug": slug,
                "name": name,
                "description": "Deterministic enrichment helper",
                "current_version": 1,
            }
        ),
        f"{SKILL_ROOT}/{source_id}/versions/1/version.yml": _yaml(
            {
                "version": 1,
                "type": "skill_version",
                "version_number": 1,
                "name": name,
                "description": "Deterministic enrichment helper",
                "files": [
                    {
                        "path": "SKILL.md",
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    }
                ],
            }
        ),
        f"{SKILL_ROOT}/{source_id}/versions/1/files/SKILL.md": content,
    }


def _versioned_agent_skill_git_tree() -> dict[str, str]:
    """Build a Git tree where two presets pin different versions of one skill.

    Layout: ``skill-a`` publishes versions 1 and 2 (head at v2), ``agent-x`` pins
    ``skill-a`` v1, and ``agent-y`` pins ``skill-a`` v2. Used to assert the import
    -> export -> pull round trip preserves the divergent pins.
    """
    skill_v1 = "# Skill A\n\nVersion 1 behavior.\n"
    skill_v2 = "# Skill A\n\nVersion 2 behavior.\n"

    def skill_version(number: int, name: str, content: str) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "skill_version",
            "version_number": number,
            "name": name,
            "files": [
                {
                    "path": "SKILL.md",
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            ],
        }

    def preset(
        source_id: str,
        name: str,
        skill_version: int,
    ) -> dict[str, str]:
        head = {
            "version": 1,
            "type": "agent_preset",
            "id": source_id,
            "slug": source_id,
            "name": name,
            "current_version": 1,
        }
        version = {
            "version": 1,
            "type": "agent_preset_version",
            "version_number": 1,
            "name": name,
            "instructions": f"Use skill-a version {skill_version}.",
            "skills": [{"slug": "skill-a", "version": skill_version}],
            "subagents": [],
            "model_name": "gpt-5.5",
            "model_provider": "openai",
        }
        return {
            f"{AGENT_PRESET_ROOT}/{source_id}/preset.yml": _yaml(head),
            f"{AGENT_PRESET_ROOT}/{source_id}/versions/1.yml": _yaml(version),
        }

    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{SKILL_ROOT}/skill-a/skill.yml": _yaml(
            {
                "version": 1,
                "type": "skill",
                "id": "skill-a",
                "slug": "skill-a",
                "name": "Skill A v2",
                "description": "Versioned skill fixture",
                "current_version": 2,
            }
        ),
        f"{SKILL_ROOT}/skill-a/versions/1/version.yml": _yaml(
            skill_version(1, "Skill A v1", skill_v1)
        ),
        f"{SKILL_ROOT}/skill-a/versions/1/files/SKILL.md": skill_v1,
        f"{SKILL_ROOT}/skill-a/versions/2/version.yml": _yaml(
            skill_version(2, "Skill A v2", skill_v2)
        ),
        f"{SKILL_ROOT}/skill-a/versions/2/files/SKILL.md": skill_v2,
        **preset("agent-x", "Agent X", 1),
        **preset("agent-y", "Agent Y", 2),
    }
    return dict(sorted(files.items()))


def _versioned_subagent_git_tree() -> dict[str, str]:
    """Build a Git tree where a parent pins a non-head subagent version."""
    subagent_ref = [{"slug": "qa-evidence-child", "version": 1}]
    parent_head = {
        "version": 1,
        "type": "agent_preset",
        "id": "qa-triage-parent",
        "slug": "qa-triage-parent",
        "name": "QA triage parent",
        "current_version": 1,
    }
    parent_version = {
        "version": 1,
        "type": "agent_preset_version",
        "version_number": 1,
        "name": "QA triage parent",
        "instructions": "Delegate to the evidence child.",
        "skills": [],
        "subagents": subagent_ref,
        "model_name": "gpt-5.5",
        "model_provider": "openai",
    }
    child_head = {
        "version": 1,
        "type": "agent_preset",
        "id": "qa-evidence-child",
        "slug": "qa-evidence-child",
        "name": "QA evidence child",
        "current_version": 2,
    }

    def child_version(number: int, instructions: str) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "agent_preset_version",
            "version_number": number,
            "name": "QA evidence child",
            "instructions": instructions,
            "skills": [],
            "subagents": [],
            "model_name": "gpt-5.5",
            "model_provider": "openai",
        }

    return dict(
        sorted(
            {
                MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
                f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml": _yaml(parent_head),
                f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml": _yaml(
                    parent_version
                ),
                f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml": _yaml(child_head),
                f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml": _yaml(
                    child_version(1, "Collect original evidence.")
                ),
                f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/2.yml": _yaml(
                    child_version(2, "Collect current evidence.")
                ),
            }.items()
        )
    )


def _workflow_pinned_agent_version_git_tree() -> dict[str, str]:
    """Build a Git tree whose workflow pins a non-head agent preset version.

    Layout: a workflow calls ``ai.preset_agent`` pinned to ``agent-x`` v1, while
    ``agent-x`` head is v2; v1 pins ``skill-a`` v1 and v2 pins ``skill-a`` v2.
    Used to assert a full export walks the workflow -> preset version -> skill
    version closure instead of exporting only the current heads.
    """
    skill_v1 = "# Skill A\n\nVersion 1 behavior.\n"
    skill_v2 = "# Skill A\n\nVersion 2 behavior.\n"

    def skill_version(number: int, name: str, content: str) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "skill_version",
            "version_number": number,
            "name": name,
            "files": [
                {
                    "path": "SKILL.md",
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            ],
        }

    def agent_version(number: int, skill_version: int) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "agent_preset_version",
            "version_number": number,
            "name": "Agent X",
            "instructions": f"Use skill-a version {skill_version}.",
            "skills": [{"slug": "skill-a", "version": skill_version}],
            "subagents": [],
            "model_name": "gpt-5.5",
            "model_provider": "openai",
        }

    return dict(
        sorted(
            {
                MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
                f"{WORKFLOW_ROOT}/workflow-pins-agent/definition.yml": _yaml(
                    _workflow_spec(
                        source_id="workflow-pins-agent",
                        title="workflow-pins-agent",
                        alias="workflow-pins-agent",
                        folder_path="QA/Root",
                        actions=[
                            {
                                "ref": "triage",
                                "action": "ai.preset_agent",
                                "args": {
                                    "preset_slug": "agent-x",
                                    "preset_version": 1,
                                    "prompt": "Use the pinned preset version.",
                                },
                            }
                        ],
                    )
                ),
                f"{AGENT_PRESET_ROOT}/agent-x/preset.yml": _yaml(
                    {
                        "version": 1,
                        "type": "agent_preset",
                        "id": "agent-x",
                        "slug": "agent-x",
                        "name": "Agent X",
                        "current_version": 2,
                    }
                ),
                f"{AGENT_PRESET_ROOT}/agent-x/versions/1.yml": _yaml(
                    agent_version(1, 1)
                ),
                f"{AGENT_PRESET_ROOT}/agent-x/versions/2.yml": _yaml(
                    agent_version(2, 2)
                ),
                f"{SKILL_ROOT}/skill-a/skill.yml": _yaml(
                    {
                        "version": 1,
                        "type": "skill",
                        "id": "skill-a",
                        "slug": "skill-a",
                        "name": "Skill A v2",
                        "description": "Versioned skill fixture",
                        "current_version": 2,
                    }
                ),
                f"{SKILL_ROOT}/skill-a/versions/1/version.yml": _yaml(
                    skill_version(1, "Skill A v1", skill_v1)
                ),
                f"{SKILL_ROOT}/skill-a/versions/1/files/SKILL.md": skill_v1,
                f"{SKILL_ROOT}/skill-a/versions/2/version.yml": _yaml(
                    skill_version(2, "Skill A v2", skill_v2)
                ),
                f"{SKILL_ROOT}/skill-a/versions/2/files/SKILL.md": skill_v2,
            }.items()
        )
    )


def _workflow_git_tree(
    *,
    source_id: str,
    alias: str,
    title: str,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{WORKFLOW_ROOT}/{source_id}/definition.yml": _yaml(
            _workflow_spec(
                source_id=source_id,
                title=title,
                alias=alias,
                folder_path="QA/Workflows",
                actions=[
                    {
                        "ref": "reshape",
                        "action": "core.transform.reshape",
                        "args": {"value": "${{ TRIGGER.value }}"},
                    }
                ],
            )
        ),
    }


def _simple_resource_rename_git_tree(
    *,
    tag_name: str,
    dropdown_name: str,
    duration_name: str,
    variable_name: str,
    secret_name: str,
) -> dict[str, str]:
    return {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{CASE_TAG_ROOT}/qa-alert.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "qa-alert",
                "name": tag_name,
                "color": "#D92D20",
            }
        ),
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml": _yaml(
            {
                "version": 1,
                "type": "case_dropdown",
                "id": "qa_resolution_reason",
                "name": dropdown_name,
                "options": [],
            }
        ),
        f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml": _yaml(
            {
                "version": 1,
                "type": "case_duration",
                "id": "qa_time_to_triage",
                "name": duration_name,
                "start": {"event": "case_created", "selection": "first"},
                "end": {"event": "status_changed", "selection": "first"},
            }
        ),
        f"{VARIABLE_ROOT}/default/qa_config.yml": _yaml(
            {
                "version": 1,
                "type": "variable",
                "id": "default/qa_config",
                "name": variable_name,
                "environment": "default",
                "keys": ["mode"],
            }
        ),
        f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml": _yaml(
            {
                "version": 1,
                "type": "secret_metadata",
                "id": "default/qa_threatintel",
                "name": secret_name,
                "environment": "default",
                "secret_type": "custom",
                "keys": ["API_KEY"],
            }
        ),
    }


def _expanded_full_git_tree(*, include_schedules: bool) -> dict[str, str]:
    skill_files = {
        "SKILL.md": (
            "# QA Enrichment Skill\n\n"
            "Use deterministic enrichment helpers for workspace sync QA.\n"
        ),
        "enrich.py": (
            "def enrich(indicator: str) -> dict[str, str]:\n"
            '    return {"indicator": indicator, "status": "seen"}\n'
        ),
    }
    files = {
        MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()),
        f"{WORKFLOW_ROOT}/qa-root/definition.yml": _yaml(
            _workflow_spec(
                source_id="qa-root",
                title="qa-root-orchestrator",
                alias="qa-root",
                folder_path="QA/Root",
                actions=[
                    {
                        "ref": "execute_child",
                        "action": "core.workflow.execute",
                        "args": {
                            "workflow_alias": "qa-child",
                            "payload": {
                                "indicator": "${{ TRIGGER.indicator }}",
                                "config": "${{ VARS.qa_config }}",
                            },
                        },
                    },
                    {
                        "ref": "triage",
                        "action": "ai.preset_agent",
                        "depends_on": ["execute_child"],
                        "args": {
                            "preset_slug": "qa-triage-parent",
                            "prompt": (
                                "Review ${{ ACTIONS.execute_child.result }} "
                                "with ${{ SECRETS.qa_threatintel.BASE_URL }}."
                            ),
                        },
                    },
                    {
                        "ref": "lookup_indicator",
                        "action": "core.table.lookup",
                        "depends_on": ["execute_child"],
                        "args": {
                            "table": "qa_indicators",
                            "column": "indicator",
                            "value": "${{ TRIGGER.indicator }}",
                        },
                    },
                ],
                include_schedules=include_schedules,
                webhook=True,
                case_trigger=True,
            )
        ),
        f"{WORKFLOW_ROOT}/qa-child/definition.yml": _yaml(
            _workflow_spec(
                source_id="qa-child",
                title="qa-child-enrichment",
                alias="qa-child",
                folder_path="QA/Children",
                actions=[
                    {
                        "ref": "enrich",
                        "action": "core.transform.reshape",
                        "args": {"value": "${{ TRIGGER.indicator }}"},
                    }
                ],
            )
        ),
        f"{WORKFLOW_ROOT}/qa-orphan/definition.yml": _yaml(
            _workflow_spec(
                source_id="qa-orphan",
                title="qa-orphan",
                alias="qa-orphan",
                folder_path="QA/Orphans",
                actions=[
                    {
                        "ref": "noop",
                        "action": "core.transform.reshape",
                        "args": {"value": "orphan"},
                    }
                ],
            )
        ),
        f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "qa-triage-parent",
                "slug": "qa-triage-parent",
                "name": "QA triage parent",
                "current_version": 1,
                "folder_path": "QA/Agents",
                "tags": ["qa-sync"],
            }
        ),
        f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "QA triage parent",
                "instructions": "Use the enrichment skill and escalate high severity.",
                "tool_approvals": {"tools.qa_enrichment.lookup": "always"},
                "actions": ["tools.qa_enrichment.lookup"],
                "skills": [{"slug": "qa-enrichment-skill", "version": 1}],
                "subagents": [{"slug": "qa-evidence-child"}],
                "model_name": "gpt-5.5",
                "model_provider": "openai",
                "base_url": "https://models.example.test/v1",
                "output_type": {"type": "json_schema", "name": "qa_triage"},
                "namespaces": ["tools.qa_enrichment"],
                "mcp_integrations": ["qa-mcp"],
                "retries": 4,
                "enable_thinking": False,
                "enable_internet_access": True,
            }
        ),
        f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset",
                "id": "qa-evidence-child",
                "slug": "qa-evidence-child",
                "name": "QA evidence child",
                "current_version": 1,
                "folder_path": "QA/Agents",
                "tags": ["qa-sync"],
            }
        ),
        f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml": _yaml(
            {
                "version": 1,
                "type": "agent_preset_version",
                "version_number": 1,
                "name": "QA evidence child",
                "instructions": "Collect concise evidence.",
                "skills": [],
                "subagents": [],
            }
        ),
        f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml": _yaml(_skill_spec(skill_files)),
        f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml": _yaml(
            {
                "version": 1,
                "type": "skill_version",
                "version_number": 1,
                "name": "QA enrichment skill",
                "description": "Deterministic enrichment helper",
                "files": [
                    {
                        "path": path,
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    }
                    for path, content in sorted(skill_files.items())
                ],
            }
        ),
        f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/files/SKILL.md": skill_files[
            "SKILL.md"
        ],
        f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/files/enrich.py": skill_files[
            "enrich.py"
        ],
        f"{TABLE_ROOT}/qa_indicators/table.yml": _yaml(
            {
                "version": 1,
                "type": "table",
                "id": "qa_indicators",
                "name": "qa_indicators",
                "columns": [
                    {"name": "indicator", "type": "text", "unique": True},
                    {
                        "name": "severity",
                        "type": "select",
                        "options": ["low", "medium", "high"],
                    },
                    {"name": "seen_at", "type": "timestamptz"},
                ],
            }
        ),
        f"{CASE_TAG_ROOT}/qa-alert.yml": _yaml(
            {
                "version": 1,
                "type": "case_tag",
                "id": "qa-alert",
                "name": "qa-alert",
                "color": "#D92D20",
            }
        ),
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml": _yaml(
            {
                "version": 1,
                "type": "case_dropdown",
                "id": "qa_resolution_reason",
                "name": "qa_resolution_reason",
                "is_ordered": True,
                "icon_name": "ListChecks",
                "position": 4,
                "required_on_closure": True,
                "options": [
                    {"ref": "benign", "label": "Benign", "position": 0},
                    {"ref": "true_positive", "label": "True positive", "position": 1},
                    {"ref": "needs_review", "label": "Needs review", "position": 2},
                ],
            }
        ),
        f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml": _yaml(
            {
                "version": 1,
                "type": "case_duration",
                "id": "qa_time_to_triage",
                "name": "qa_time_to_triage",
                "start": {"event": "case_created", "selection": "first"},
                "end": {"event": "status_changed", "selection": "first"},
            }
        ),
        f"{CASE_FIELD_ROOT}/qa_external_ref.yml": _yaml(
            {
                "version": 1,
                "type": "case_field",
                "id": "qa_external_ref",
                "name": "qa_external_ref",
                "field_type": "text",
                "kind": "short_text",
            }
        ),
        f"{VARIABLE_ROOT}/default/qa_config.yml": _yaml(
            {
                "version": 1,
                "type": "variable",
                "id": "default/qa_config",
                "name": "qa_config",
                "environment": "default",
                "keys": ["mode", "threshold"],
                "description": "QA config variable",
                "tags": ["qa-sync"],
            }
        ),
        f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml": _yaml(
            {
                "version": 1,
                "type": "secret_metadata",
                "id": "default/qa_threatintel",
                "name": "qa_threatintel",
                "environment": "default",
                "secret_type": "custom",
                "keys": ["API_KEY", "BASE_URL"],
                "tags": ["qa-sync"],
                "description": "QA threat intel credentials",
            }
        ),
    }
    return dict(sorted(files.items()))


def _expanded_selected_git_tree() -> dict[str, str]:
    files = deepcopy(_expanded_full_git_tree(include_schedules=False))
    for path in (
        f"{WORKFLOW_ROOT}/qa-orphan/definition.yml",
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml",
        f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml",
        f"{CASE_FIELD_ROOT}/qa_external_ref.yml",
    ):
        del files[path]
    return dict(sorted(files.items()))


def _expected_full_paths() -> set[str]:
    return {
        MANIFEST_FILENAME,
        f"{WORKFLOW_ROOT}/qa-root/definition.yml",
        f"{WORKFLOW_ROOT}/qa-child/definition.yml",
        f"{WORKFLOW_ROOT}/qa-orphan/definition.yml",
        f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml",
        f"{AGENT_PRESET_ROOT}/qa-triage-parent/versions/1.yml",
        f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml",
        f"{AGENT_PRESET_ROOT}/qa-evidence-child/versions/1.yml",
        f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml",
        f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/version.yml",
        f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/files/SKILL.md",
        f"{SKILL_ROOT}/qa-enrichment-skill/versions/1/files/enrich.py",
        f"{TABLE_ROOT}/qa_indicators/table.yml",
        f"{CASE_TAG_ROOT}/qa-alert.yml",
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml",
        f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml",
        f"{CASE_FIELD_ROOT}/qa_external_ref.yml",
        f"{VARIABLE_ROOT}/default/qa_config.yml",
        f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml",
    }


def _workflow_spec(
    *,
    source_id: str,
    title: str,
    alias: str,
    folder_path: str,
    actions: list[dict[str, Any]],
    include_schedules: bool = False,
    webhook: bool = False,
    case_trigger: bool = False,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "version": 1,
        "type": "workflow",
        "id": source_id,
        "alias": alias,
        "folder_path": folder_path,
        "tags": [{"name": "qa-sync"}],
        "definition": {
            "title": title,
            "description": f"{title} workspace sync QA fixture",
            "entrypoint": {"ref": actions[0]["ref"], "expects": {}},
            "actions": actions,
        },
    }
    if webhook:
        spec["webhook"] = {
            "methods": ["POST"],
            "status": "online",
            "include_headers": False,
        }
    if case_trigger:
        spec["case_trigger"] = {
            "status": "online",
            "event_types": ["case_created"],
            "tag_filters": ["qa-alert"],
        }
    if include_schedules:
        spec["schedules"] = [
            {
                "status": "offline",
                "cron": "0 8 * * *",
                "timeout": 300,
            }
        ]
    return spec


def _skill_spec(skill_files: dict[str, str]) -> dict[str, Any]:
    return {
        "version": 1,
        "type": "skill",
        "id": "qa-enrichment-skill",
        "slug": "qa-enrichment-skill",
        "name": "QA enrichment skill",
        "description": "Deterministic enrichment helper",
        "current_version": 1,
    }


def _assert_secret_metadata_has_no_values(files: dict[str, str]) -> None:
    for path, content in files.items():
        if not path.startswith(f"{SECRET_METADATA_ROOT}/"):
            continue
        secret_spec = yaml.safe_load(content)
        assert "value" not in secret_spec
        assert "values" not in secret_spec
        assert secret_spec["keys"] == ["API_KEY", "BASE_URL"]


def _assert_variable_exports_have_no_values(files: dict[str, str]) -> None:
    for path, content in files.items():
        if not path.startswith(f"{VARIABLE_ROOT}/"):
            continue
        variable_spec = yaml.safe_load(content)
        assert "value" not in variable_spec
        assert "values" not in variable_spec
        assert variable_spec["keys"] == ["mode", "threshold"]


def _assert_workflows_have_no_schedules(files: dict[str, str]) -> None:
    for path, content in files.items():
        if not path.startswith(f"{WORKFLOW_ROOT}/"):
            continue
        workflow_spec = yaml.safe_load(content)
        assert "schedules" not in workflow_spec


def _yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)
