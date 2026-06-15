"""Acceptance contract for expanded workspace sync resources.

These tests encode the all-config-resource QA plan while the implementation is
still workflow-only. The static contract tests should pass today; adapter and
reconciler tests are xfailed until those resource handlers exist.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    CaseTag,
    Secret,
    Skill,
    Table,
    WorkspaceVariable,
)
from tracecat.git.types import GitUrl
from tracecat.sync import PullOptions
from tracecat.workspace_sync.enums import SyncResourceType
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

    assert await session.scalar(
        select(Skill).where(
            Skill.workspace_id == workspace_id,
            Skill.name == "qa-enrichment-skill",
        )
    )
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
                "value": {"mode": "qa", "threshold": 7},
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


def _assert_workflows_have_no_schedules(files: dict[str, str]) -> None:
    for path, content in files.items():
        if not path.startswith(f"{WORKFLOW_ROOT}/"):
            continue
        workflow_spec = yaml.safe_load(content)
        assert "schedules" not in workflow_spec


def _yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)
