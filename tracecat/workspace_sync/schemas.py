"""Schemas for canonical workspace Git sync specs and APIs."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.core.schemas import Schema
from tracecat.dsl.common import DSLInput
from tracecat.sync import CommitInfo, PullDiagnostic
from tracecat.workflow.store.schemas import (
    RemoteCaseTrigger,
    RemoteWebhook,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
    WorkflowDslPublishResult,
)
from tracecat.workspace_sync.enums import SyncOperation, SyncStateStatus

MANIFEST_FILENAME = "tracecat.json"
WORKFLOW_ROOT = "workflows"
WORKFLOW_DEFINITION_FILENAME = "definition.yml"


class WorkspaceManifestResources(BaseModel):
    workflows: str = f"{WORKFLOW_ROOT}/"


class WorkspaceManifest(BaseModel):
    version: Literal[1] = 1
    resources: WorkspaceManifestResources = Field(
        default_factory=WorkspaceManifestResources
    )


class WorkflowResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a workflow resource."""

    version: Literal[1] = 1
    type: Literal["workflow"] = "workflow"
    id: str = Field(min_length=1)
    alias: str | None = None
    folder_path: str | None = None
    tags: list[RemoteWorkflowTag] | None = None
    schedules: list[RemoteWorkflowSchedule] | None = None
    webhook: RemoteWebhook | None = None
    case_trigger: RemoteCaseTrigger | None = None
    definition: DSLInput

    @field_validator("id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        cleaned = value.strip().strip("/")
        if not cleaned:
            raise ValueError("workflow source id cannot be empty")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("workflow source id must be a single path segment")
        return cleaned


class WorkspaceSpec(BaseModel):
    version: Literal[1] = 1
    workflows: dict[str, WorkflowResourceSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_workflow_keys(self) -> WorkspaceSpec:
        for source_id, spec in self.workflows.items():
            if source_id != spec.id:
                raise ValueError(
                    f"Workflow map key {source_id!r} does not match spec id {spec.id!r}"
                )
        return self


class ProjectedFile(BaseModel):
    path: str
    content: str


class WorkspaceProjection(BaseModel):
    manifest: WorkspaceManifest
    spec: WorkspaceSpec
    files: dict[str, str]
    spec_hash: str


class WorkspaceRemoteSnapshot(BaseModel):
    commit_sha: str
    tree_sha: str | None = None
    files: dict[str, str]
    spec: WorkspaceSpec
    spec_hash: str


class ResourceRef(BaseModel):
    resource_type: str
    source_id: str
    source_path: str | None = None
    local_id: uuid.UUID | None = None


class WorkspaceSyncStatus(BaseModel):
    status: SyncStateStatus
    base_spec_hash: str | None
    local_spec_hash: str
    remote_spec_hash: str | None = None
    base_commit_sha: str | None = None
    remote_commit_sha: str | None = None
    target_ref: str | None = None
    pending_change_count: int = 0
    diagnostics: list[PullDiagnostic] = Field(default_factory=list)


class WorkspaceSyncPullPreview(BaseModel):
    success: bool
    commit_sha: str
    workflows_found: int
    diagnostics: list[PullDiagnostic] = Field(default_factory=list)
    message: str


class WorkspaceSyncPullRequest(BaseModel):
    commit_sha: str = Field(min_length=40, max_length=64)
    dry_run: bool = False
    force: bool = False


class ChangeSetCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    resources: list[ResourceRef]


class ChangeSetExport(BaseModel):
    message: str
    branch: str
    create_pr: bool = False
    pr_base_branch: str | None = None


class WorkspaceSyncPendingChange(BaseModel):
    resource_type: str
    source_id: str
    source_path: str
    local_id: uuid.UUID | None = None
    operation: SyncOperation
    title: str | None = None
    alias: str | None = None
    before_spec_hash: str | None = None
    after_spec_hash: str | None = None
    exportable: bool = True


class WorkspaceSyncPendingChanges(BaseModel):
    base_spec_hash: str | None = None
    local_spec_hash: str
    changes: list[WorkspaceSyncPendingChange] = Field(default_factory=list)


class ChangeSetRead(Schema):
    id: uuid.UUID
    title: str
    description: str | None = None
    base_commit_sha: str | None = None
    base_spec_hash: str | None = None
    selected_resources: list[dict[str, Any]]
    selected_paths: list[str]
    validation_status: str
    validation_result: dict[str, Any]
    status: str


class WorkspaceSyncExportResult(BaseModel):
    changeset_id: uuid.UUID
    commit: CommitInfo

    def as_workflow_publish_result(self) -> WorkflowDslPublishResult:
        return WorkflowDslPublishResult(
            status=self.commit.status.value,
            commit_sha=self.commit.sha,
            branch=self.commit.ref,
            base_branch=self.commit.base_ref,
            pr_url=self.commit.pr_url,
            pr_number=self.commit.pr_number,
            pr_reused=self.commit.pr_reused,
            message=self.commit.message,
        )
