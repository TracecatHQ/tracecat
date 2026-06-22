"""Acceptance contract for expanded workspace sync resources.

These tests encode the all-config-resource QA plan for the workspace sync
resource adapter and reconciler implementations.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import (
    AgentCatalog,
    AgentPreset,
    AgentPresetVersion,
    CaseDropdownDefinition,
    CaseDurationDefinition,
    CaseFields,
    CaseTag,
    Secret,
    Skill,
    SkillVersion,
    Table,
    Workflow,
    Workspace,
    WorkspaceSyncResourceMapping,
    WorkspaceVariable,
)
from tracecat.git.types import GitUrl
from tracecat.pagination import CursorPaginationParams
from tracecat.registry.lock.types import RegistryLock
from tracecat.sync import PullOptions, PushStatus
from tracecat.tables.schemas import TableUpdate
from tracecat.tables.service import BaseTablesService
from tracecat.workspace_sync.adapters import WORKSPACE_RESOURCE_ADAPTERS
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
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
    WorkspaceManifest,
    WorkspaceProjection,
    WorkspaceSpec,
    WorkspaceSyncExportRequest,
    manifest_resource_roots,
)
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceSyncService
from tracecat.workspace_sync.transport import VcsTreeSnapshot

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
    assert f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml" in files
    assert f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml" in files
    assert f"{TABLE_ROOT}/qa_indicators/table.yml" in files
    assert f"{CASE_TAG_ROOT}/qa-alert.yml" in files
    assert f"{VARIABLE_ROOT}/default/qa_config.yml" in files
    assert f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml" in files
    assert not any(path.startswith(f"{CASE_DROPDOWN_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_DURATION_ROOT}/") for path in files)
    assert not any(path.startswith(f"{CASE_FIELD_ROOT}/") for path in files)


def test_skill_fixture_records_file_sha256s() -> None:
    files = _expanded_full_git_tree(include_schedules=False)
    skill_spec = yaml.safe_load(files[f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml"])

    recorded_hashes = {file["path"]: file["sha256"] for file in skill_spec["files"]}
    for file_path, expected_hash in recorded_hashes.items():
        content = files[f"{SKILL_ROOT}/qa-enrichment-skill/files/{file_path}"]
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
    assert len(_future_attr(snapshot.spec, "agent_presets")) == 2
    assert len(_future_attr(snapshot.spec, "skills")) == 1
    assert len(_future_attr(snapshot.spec, "tables")) == 1
    assert len(_future_attr(snapshot.spec, "case_tags")) == 1
    assert len(_future_attr(snapshot.spec, "case_dropdowns")) == 1
    assert len(_future_attr(snapshot.spec, "case_durations")) == 1
    assert len(_future_attr(snapshot.spec, "case_fields")) == 1
    assert len(_future_attr(snapshot.spec, "variables")) == 1
    assert len(_future_attr(snapshot.spec, "secret_metadata")) == 1


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
    resource_counts = _future_attr(result, "resource_counts")
    assert resource_counts["workflow"].found == 2
    assert resource_counts["agent_preset"].found == 2
    assert resource_counts["skill"].found == 1
    assert resource_counts["table"].found == 1
    assert resource_counts["case_tag"].found == 1
    assert resource_counts["variable"].found == 1
    assert resource_counts["secret_metadata"].found == 1


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
async def test_cyclic_preset_subagent_references_are_dependency_diagnostics(
    workspace_sync_service: WorkspaceSyncService,
) -> None:
    files = _expanded_selected_git_tree()
    child_path = f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml"
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

    table_rows = [
        json.loads(line)
        for line in files[f"{TABLE_ROOT}/qa_indicators/rows.jsonl"].splitlines()
    ]
    assert {row["indicator"] for row in table_rows} == {
        "bad.example",
        "hash:0123456789abcdef",
    }
    skill_spec = yaml.safe_load(files[f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml"])
    assert {file["path"] for file in skill_spec["files"]} == {
        "SKILL.md",
        "enrich.py",
    }
    parent_preset = yaml.safe_load(
        files[f"{AGENT_PRESET_ROOT}/qa-triage-parent/preset.yml"]
    )
    assert parent_preset["folder_path"] == "/QA/Agents/"
    assert parent_preset["tags"] == ["qa-sync"]


@pytest.mark.anyio
async def test_source_export_target_pull_preserves_projected_workspace(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Round-trip a workspace through VCS and assert target parity.

    This is the black-box acceptance flow for workspace sync: a source workspace
    pushes canonical sync files to a shared repo, a target workspace pulls them,
    and both workspaces project back to the same canonical file set. The second
    push/pull cycle proves in-place updates preserve parity after renames,
    metadata edits, and an added resource.
    """
    assert svc_role.workspace_id is not None
    repo_url = "git+ssh://git@github.com/TracecatHQ/git-sync-qa.git"
    git_url = GitUrl(host="github.com", org="TracecatHQ", repo="git-sync-qa")
    fake_vcs = FakeVcsServer()
    await _set_workspace_git_repo_url(
        session,
        workspace_id=svc_role.workspace_id,
        repo_url=repo_url,
    )
    target_role = await _create_workspace_role(
        session,
        source_role=svc_role,
        workspace_name="target-workspace",
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
            options=PullOptions(commit_sha=seed_commit.sha)
        )
        assert source_pull.success is True

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
            options=PullOptions(commit_sha=first_export.commit.sha)
        )
        assert first_target_pull.success is True
        await _assert_projected_workspaces_match(source_service, target_service)

        # Exercise a representative update batch before the second push/pull:
        # mapped-resource renames, metadata edits, and one new resource.
        await _mutate_source_workspace_for_roundtrip_update(session, role=svc_role)
        second_export = await source_service.export_workspace(
            WorkspaceSyncExportRequest(
                message="Push source workspace update",
                branch="sync/source-to-target",
                create_pr=False,
            )
        )
        assert second_export.commit.status is PushStatus.COMMITTED
        assert second_export.commit.sha is not None
        # Confirm the fake VCS commit stores exactly what source projection emits.
        assert (
            fake_vcs.repo_files(git_url, ref=second_export.commit.sha)
            == (
                await source_service.project_workspace(create_missing_mappings=False)
            ).files
        )

        second_target_pull = await target_service.pull(
            options=PullOptions(commit_sha=second_export.commit.sha)
        )

    assert second_target_pull.success is True
    await _assert_projected_workspaces_match(source_service, target_service)


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
    assert tag is not None
    assert dropdown is not None
    assert duration is not None
    assert variable is not None
    assert secret is not None

    tag.name = "QA alert renamed"
    tag.ref = "qa-alert-renamed"
    dropdown.name = "QA resolution reason renamed"
    dropdown.ref = "qa_resolution_reason_renamed"
    duration.name = "qa_time_to_triage_renamed"
    variable.name = "qa_config_renamed"
    secret.name = "qa_threatintel_renamed"
    session.add_all([tag, dropdown, duration, variable, secret])
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
        model_name="gpt-4.1-mini",
    )
    session.add(catalog)
    await session.flush()
    files = _agent_preset_git_tree(
        source_id="qa-catalog-backed",
        slug="qa-catalog-backed",
        name="QA catalog backed",
    )
    preset_path = f"{AGENT_PRESET_ROOT}/qa-catalog-backed/preset.yml"
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

    projection = await service.project_workspace(create_missing_mappings=False)
    field_spec = yaml.safe_load(
        projection.files[f"{CASE_FIELD_ROOT}/severity_band.yml"]
    )
    assert field_spec["options"] == ["low", "medium", "high"]


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
async def test_table_import_inserts_rows_without_unique_column(
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

    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session,
        role=svc_role,
    ).import_non_workflow_resources(snapshot.spec)

    table_service = BaseTablesService(session=session, role=svc_role)
    table = await table_service.get_table_by_name("qa_non_unique")
    rows = await table_service.list_rows(
        table,
        CursorPaginationParams(limit=10),
        order_by="indicator",
        sort="asc",
    )
    assert [row["indicator"] for row in rows.items] == ["alpha", "beta"]


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


async def _set_workspace_git_repo_url(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_url: str,
) -> None:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
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
) -> Role:
    assert source_role.organization_id is not None
    workspace = Workspace(
        name=workspace_name,
        organization_id=source_role.organization_id,
        settings={"git_repo_url": repo_url},
    )
    session.add(workspace)
    await session.flush()
    return source_role.model_copy(update={"workspace_id": workspace.id})


async def _assert_projected_workspaces_match(
    source_service: WorkspaceSyncService,
    target_service: WorkspaceSyncService,
) -> None:
    """Compare canonical sync files, not DB IDs or other local-only state."""
    source_projection = await source_service.project_workspace(
        create_missing_mappings=False
    )
    target_projection = await target_service.project_workspace(
        create_missing_mappings=False
    )
    assert target_projection.files == source_projection.files


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


def _agent_preset_git_tree(
    *,
    source_id: str,
    slug: str,
    name: str,
    instructions: str | None = None,
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
                "instructions": instructions,
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
                "rows_path": None,
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
                "files": [
                    {
                        "path": "SKILL.md",
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    }
                ],
            }
        ),
        f"{SKILL_ROOT}/{source_id}/files/SKILL.md": content,
    }


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
                                "with ${{ SECRETS.qa_threatintel.BASE_URL }} "
                                "and table qa_indicators."
                            ),
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
                "folder_path": "QA/Agents",
                "tags": ["qa-sync"],
                "instructions": "Use the enrichment skill and escalate high severity.",
                "tool_approvals": {"tools.qa_enrichment.lookup": "always"},
                "actions": ["tools.qa_enrichment.lookup"],
                "skills": [{"slug": "qa-enrichment-skill", "version": 1}],
                "subagents": [{"slug": "qa-evidence-child"}],
                "model_name": "gpt-4.1-mini",
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
                "folder_path": "QA/Agents",
                "tags": ["qa-sync"],
                "instructions": "Collect concise evidence.",
                "skills": [],
                "subagents": [],
            }
        ),
        f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml": _yaml(_skill_spec(skill_files)),
        f"{SKILL_ROOT}/qa-enrichment-skill/files/SKILL.md": skill_files["SKILL.md"],
        f"{SKILL_ROOT}/qa-enrichment-skill/files/enrich.py": skill_files["enrich.py"],
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
                "rows_path": "rows.jsonl",
            }
        ),
        f"{TABLE_ROOT}/qa_indicators/rows.jsonl": "\n".join(
            [
                json.dumps(
                    {
                        "indicator": "bad.example",
                        "severity": "high",
                        "seen_at": "2026-06-14T00:00:00Z",
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "indicator": "hash:0123456789abcdef",
                        "severity": "medium",
                        "seen_at": "2026-06-14T01:00:00Z",
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
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
        f"{AGENT_PRESET_ROOT}/qa-evidence-child/preset.yml",
        f"{SKILL_ROOT}/qa-enrichment-skill/skill.yml",
        f"{SKILL_ROOT}/qa-enrichment-skill/files/SKILL.md",
        f"{SKILL_ROOT}/qa-enrichment-skill/files/enrich.py",
        f"{TABLE_ROOT}/qa_indicators/table.yml",
        f"{TABLE_ROOT}/qa_indicators/rows.jsonl",
        f"{CASE_TAG_ROOT}/qa-alert.yml",
        f"{CASE_DROPDOWN_ROOT}/qa_resolution_reason.yml",
        f"{CASE_DURATION_ROOT}/qa_time_to_triage.yml",
        f"{CASE_FIELD_ROOT}/qa_external_ref.yml",
        f"{VARIABLE_ROOT}/default/qa_config.yml",
        f"{SECRET_METADATA_ROOT}/default/qa_threatintel.yml",
    }


def _future_attr(obj: object, attr: str) -> Any:
    return getattr(obj, attr)


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
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
            for path, content in sorted(skill_files.items())
        ],
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
