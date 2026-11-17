"""Exact prepared permissions stay compact and separate from resource contents."""

from datetime import UTC, datetime

from tracecat.db.models import WorkspaceSyncOperation
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.sync import PullResult
from tracecat.workflow.store.schemas import RemoteForm
from tracecat.workspace_sync.operations.domain import preview_summary
from tracecat.workspace_sync.operations.lifecycle_activities import _prepared_ref
from tracecat.workspace_sync.operations.schemas import PreparedSync
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    VariableResourceSpec,
    WorkflowResourceSpec,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
)


def test_pull_preview_indexes_exact_write_permissions():
    sample_dsl = DSLInput(
        title="Synthetic",
        description="Synthetic",
        entrypoint=DSLEntrypoint(ref="reshape"),
        actions=[
            ActionStatement(
                ref="reshape",
                action="core.transform.reshape",
                args={"value": "synthetic"},
                depends_on=[],
            )
        ],
    )
    spec = WorkspaceSpec(
        variables={
            "synthetic": VariableResourceSpec(
                id="synthetic", name="synthetic", environment="default"
            )
        },
        agent_presets={
            "synthetic": AgentPresetResourceSpec(
                id="synthetic",
                slug="synthetic",
                name="Synthetic",
                is_workspace_default=True,
            )
        },
        workflows={
            "synthetic": WorkflowResourceSpec(
                id="synthetic",
                definition=sample_dsl,
                form=RemoteForm(title="Synthetic"),
            )
        },
    )
    prepared = PreparedSync(
        snapshot=WorkspaceRemoteSnapshot(commit_sha="a" * 40, files={}, spec=spec),
        preview=PullResult(
            success=True,
            commit_sha="a" * 40,
            workflows_found=1,
            workflows_imported=0,
            diagnostics=[],
            message="Synthetic preview",
        ),
        workspace_fingerprint="workspace",
        repository_fingerprint="repo",
        compare_ref="main",
        compare_sha="a" * 40,
    )
    summary = preview_summary(prepared)
    assert set(summary["write_scopes"]) == {
        "variable:create",
        "variable:update",
        "agent:create",
        "agent:update",
        "workflow:create",
        "workflow:update",
        "form:create",
        "form:update",
        "workspace:update",
    }
    operation = WorkspaceSyncOperation(
        artifact_key="synthetic",
        commit_sha="a" * 40,
        expires_at=datetime.now(UTC),
        summary=summary,
    )
    assert _prepared_ref(operation).write_scopes == summary["write_scopes"]


def test_older_prepared_reference_has_no_permission_metadata():
    operation = WorkspaceSyncOperation(
        artifact_key="synthetic",
        commit_sha="a" * 40,
        expires_at=datetime.now(UTC),
        summary={},
    )
    assert _prepared_ref(operation).write_scopes is None
