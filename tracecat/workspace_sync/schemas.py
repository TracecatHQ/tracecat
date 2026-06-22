"""Schemas for workspace VCS import/export."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import CaseEventType
from tracecat.dsl.common import DSLInput
from tracecat.sync import CommitInfo
from tracecat.workflow.store.schemas import (
    RemoteCaseTrigger,
    RemoteWebhook,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
    WorkflowDslPublishResult,
)
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider

MANIFEST_FILENAME = "tracecat.json"
WORKFLOW_ROOT = "workflows"
AGENT_PRESET_ROOT = "agent_presets"
SKILL_ROOT = "skills"
TABLE_ROOT = "tables"
CASE_TAG_ROOT = "case_tags"
CASE_FIELD_ROOT = "case_fields"
CASE_DROPDOWN_ROOT = "case_dropdowns"
CASE_DURATION_ROOT = "case_durations"
VARIABLE_ROOT = "variables"
SECRET_METADATA_ROOT = "secret_metadata"
WORKFLOW_DEFINITION_FILENAME = "definition.yml"


class WorkspaceManifestResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflows: str = f"{WORKFLOW_ROOT}/"
    agent_presets: str = f"{AGENT_PRESET_ROOT}/"
    skills: str = f"{SKILL_ROOT}/"
    tables: str = f"{TABLE_ROOT}/"
    case_tags: str = f"{CASE_TAG_ROOT}/"
    case_fields: str = f"{CASE_FIELD_ROOT}/"
    case_dropdowns: str = f"{CASE_DROPDOWN_ROOT}/"
    case_durations: str = f"{CASE_DURATION_ROOT}/"
    variables: str = f"{VARIABLE_ROOT}/"
    secret_metadata: str = f"{SECRET_METADATA_ROOT}/"


class WorkspaceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    resources: WorkspaceManifestResources = Field(
        default_factory=WorkspaceManifestResources
    )


def workspace_manifest_from_json(content: str) -> WorkspaceManifest:
    """Parse current and legacy workspace sync manifests.

    Older workflow-only sync repos wrote ``{"version": "1"}``. The simpler
    all-resource format uses a numeric literal, but accepting the legacy string
    keeps existing workflow repos importable.
    """
    try:
        return WorkspaceManifest.model_validate_json(content)
    except Exception as original_error:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as decode_error:
            raise original_error from decode_error

        if not isinstance(raw, dict) or raw.get("version") != "1":
            raise original_error

        return WorkspaceManifest.model_validate({**raw, "version": 1})


def manifest_resource_roots(manifest: WorkspaceManifest) -> tuple[str, ...]:
    """Return normalized manifest resource roots."""
    return tuple(
        root.strip("/")
        for root in manifest.resources.model_dump(mode="json").values()
        if root.strip("/")
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


class AgentPresetSkillBinding(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)


class AgentPresetSubagentRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str = Field(min_length=1)


class AgentPresetResourceSpec(BaseModel):
    """Canonical Git-owned desired state for an agent preset."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["agent_preset"] = "agent_preset"
    id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    folder_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    instructions: str | None = None
    tool_approvals: dict[str, Any] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    skills: list[AgentPresetSkillBinding] = Field(default_factory=list)
    subagents: list[AgentPresetSubagentRef] = Field(default_factory=list)
    catalog_id: uuid.UUID | None = None
    model_name: str | None = None
    model_provider: str | None = None
    base_url: str | None = None
    output_type: Any | None = None
    namespaces: list[str] = Field(default_factory=list)
    mcp_integrations: list[str] = Field(default_factory=list)
    retries: int = Field(default=3, ge=0)
    enable_thinking: bool = True
    enable_internet_access: bool = False


class SkillFileSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)


class SkillResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a published skill."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["skill"] = "skill"
    id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    current_version: int | None = Field(default=None, ge=1)
    files: list[SkillFileSpec] = Field(default_factory=list)
    file_contents: dict[str, str] = Field(default_factory=dict, exclude=True)


class TableResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a table schema and optional rows."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["table"] = "table"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows_path: str | None = "rows.jsonl"
    rows: list[dict[str, Any]] = Field(default_factory=list, exclude=True)


class CaseTagResourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["case_tag"] = "case_tag"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    color: str | None = None


class CaseDropdownResourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["case_dropdown"] = "case_dropdown"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    options: list[dict[str, Any]] = Field(default_factory=list)
    is_ordered: bool = False
    icon_name: str | None = None
    position: int = 0
    required_on_closure: bool = False


class CaseDurationAnchorSpec(BaseModel):
    """Event boundary describing one end of a case duration."""

    model_config = ConfigDict(extra="forbid")

    event: CaseEventType
    selection: CaseDurationAnchorSelection = CaseDurationAnchorSelection.FIRST
    timestamp_path: str = "created_at"
    field_filters: dict[str, Any] = Field(default_factory=dict)


class CaseDurationResourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    type: Literal["case_duration"] = "case_duration"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    start: CaseDurationAnchorSpec
    end: CaseDurationAnchorSpec


class CaseFieldResourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["case_field"] = "case_field"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    field_type: str | None = None
    kind: str | None = None
    options: list[str] | None = None
    required_on_closure: bool = False


class VariableResourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["variable"] = "variable"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    keys: list[str] | None = None
    value: Any | None = Field(default=None, exclude=True)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class SecretMetadataResourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Literal[1] = 1
    type: Literal["secret_metadata"] = "secret_metadata"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    secret_type: str | None = None
    keys: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_values(cls, data: Any) -> Any:
        if isinstance(data, dict) and ({"value", "values"} & data.keys()):
            raise ValueError("secret value material is not allowed in Git")
        return data


class WorkspaceSpec(BaseModel):
    version: Literal[1] = 1
    workflows: dict[str, WorkflowResourceSpec] = Field(default_factory=dict)
    agent_presets: dict[str, AgentPresetResourceSpec] = Field(default_factory=dict)
    skills: dict[str, SkillResourceSpec] = Field(default_factory=dict)
    tables: dict[str, TableResourceSpec] = Field(default_factory=dict)
    case_tags: dict[str, CaseTagResourceSpec] = Field(default_factory=dict)
    case_fields: dict[str, CaseFieldResourceSpec] = Field(default_factory=dict)
    case_dropdowns: dict[str, CaseDropdownResourceSpec] = Field(default_factory=dict)
    case_durations: dict[str, CaseDurationResourceSpec] = Field(default_factory=dict)
    variables: dict[str, VariableResourceSpec] = Field(default_factory=dict)
    secret_metadata: dict[str, SecretMetadataResourceSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_resource_keys(self) -> WorkspaceSpec:
        resources: tuple[tuple[str, dict[str, Any]], ...] = (
            ("Workflow", self.workflows),
            ("Agent preset", self.agent_presets),
            ("Skill", self.skills),
            ("Table", self.tables),
            ("Case tag", self.case_tags),
            ("Case field", self.case_fields),
            ("Case dropdown", self.case_dropdowns),
            ("Case duration", self.case_durations),
            ("Variable", self.variables),
            ("Secret metadata", self.secret_metadata),
        )
        for resource_label, specs in resources:
            for source_id, spec in specs.items():
                spec_id = spec.id
                if source_id == spec_id:
                    continue
                raise ValueError(
                    f"{resource_label} map key {source_id!r} does not match "
                    f"spec id {spec_id!r}"
                )
        return self

    def resource_count_map(self) -> dict[str, int]:
        return {
            SyncResourceType.WORKFLOW.value: len(self.workflows),
            SyncResourceType.AGENT_PRESET.value: len(self.agent_presets),
            SyncResourceType.SKILL.value: len(self.skills),
            SyncResourceType.TABLE.value: len(self.tables),
            SyncResourceType.CASE_TAG.value: len(self.case_tags),
            SyncResourceType.CASE_FIELD.value: len(self.case_fields),
            SyncResourceType.CASE_DROPDOWN.value: len(self.case_dropdowns),
            SyncResourceType.CASE_DURATION.value: len(self.case_durations),
            SyncResourceType.VARIABLE.value: len(self.variables),
            SyncResourceType.SECRET_METADATA.value: len(self.secret_metadata),
        }


class WorkspaceProjection(BaseModel):
    manifest: WorkspaceManifest
    spec: WorkspaceSpec
    files: dict[str, str]


class WorkspaceRemoteSnapshot(BaseModel):
    commit_sha: str
    tree_sha: str | None = None
    files: dict[str, str]
    spec: WorkspaceSpec


class ResourceRef(BaseModel):
    resource_type: SyncResourceType
    source_id: str | None = None
    local_id: uuid.UUID | None = None


class WorkspaceSyncExportRequest(BaseModel):
    message: str = Field(min_length=1)
    branch: str
    create_pr: bool = False
    pr_base_branch: str | None = None
    resources: list[ResourceRef] | None = None
    provider: VcsProvider = VcsProvider.GITHUB
    include_schedules: bool = False

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be empty or whitespace")
        return cleaned


class WorkspaceSyncExportPreviewRequest(BaseModel):
    """Request a dry-run projection of what an export would push to Git."""

    resources: list[ResourceRef] | None = None
    include_schedules: bool = False


class WorkspaceSyncExportPreview(BaseModel):
    """Projection summary of the resources an export would commit.

    Mirrors the pull dry-run preview: it projects the selected resources
    locally without writing to Git or mutating sync mappings.
    """

    resource_counts: dict[str, int]
    files: list[str]


class WorkspaceSyncExportResult(BaseModel):
    commit: CommitInfo
    files: list[str]

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
