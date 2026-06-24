"""Schemas for workspace VCS import/export."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import CaseEventType
from tracecat.dsl.common import DSLInput
from tracecat.sync import CommitInfo, PullResourceDiff
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
    """Repository root directory for each synced resource type."""

    model_config = ConfigDict(extra="forbid")

    workflows: str = Field(
        default=f"{WORKFLOW_ROOT}/",
        description="Repository-relative root directory for workflow files.",
    )
    agent_presets: str = Field(
        default=f"{AGENT_PRESET_ROOT}/",
        description="Repository-relative root directory for agent preset files.",
    )
    skills: str = Field(
        default=f"{SKILL_ROOT}/",
        description="Repository-relative root directory for skill files.",
    )
    tables: str = Field(
        default=f"{TABLE_ROOT}/",
        description="Repository-relative root directory for table files.",
    )
    case_tags: str = Field(
        default=f"{CASE_TAG_ROOT}/",
        description="Repository-relative root directory for case tag files.",
    )
    case_fields: str = Field(
        default=f"{CASE_FIELD_ROOT}/",
        description="Repository-relative root directory for case field files.",
    )
    case_dropdowns: str = Field(
        default=f"{CASE_DROPDOWN_ROOT}/",
        description="Repository-relative root directory for case dropdown files.",
    )
    case_durations: str = Field(
        default=f"{CASE_DURATION_ROOT}/",
        description="Repository-relative root directory for case duration files.",
    )
    variables: str = Field(
        default=f"{VARIABLE_ROOT}/",
        description="Repository-relative root directory for variable files.",
    )
    secret_metadata: str = Field(
        default=f"{SECRET_METADATA_ROOT}/",
        description="Repository-relative root directory for secret metadata files.",
    )


class WorkspaceManifest(BaseModel):
    """Top-level ``tracecat.json`` describing a workspace sync repository."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = Field(default=1, description="Manifest schema version.")
    resources: WorkspaceManifestResources = Field(
        default_factory=WorkspaceManifestResources,
        description="Per-resource-type repository root directories.",
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

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["workflow"] = Field(
        default="workflow", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the workflow's single-segment file path key.",
    )
    alias: str | None = Field(
        default=None,
        description="Optional human-friendly alias used for cross-references.",
    )
    folder_path: str | None = Field(
        default=None,
        description="Workspace folder the workflow lives under, if any.",
    )
    tags: list[RemoteWorkflowTag] | None = Field(
        default=None,
        description="Workflow tags, or ``None`` when tags are not synced.",
    )
    schedules: list[RemoteWorkflowSchedule] | None = Field(
        default=None,
        description="Workflow schedules, or ``None`` when schedules are not synced.",
    )
    webhook: RemoteWebhook | None = Field(
        default=None,
        description="Inbound webhook trigger config, if the workflow has one.",
    )
    case_trigger: RemoteCaseTrigger | None = Field(
        default=None,
        description="Case-event trigger config, if the workflow has one.",
    )
    definition: DSLInput = Field(
        description="The workflow DSL: the executable graph and its metadata.",
    )

    @field_validator("id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        """Require the workflow id to be a non-empty single path segment."""
        cleaned = value.strip().strip("/")
        if not cleaned:
            raise ValueError("workflow source id cannot be empty")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("workflow source id must be a single path segment")
        return cleaned


class AgentPresetSkillBinding(BaseModel):
    """Reference from an agent preset to a skill, optionally version-pinned."""

    model_config = ConfigDict(extra="allow")

    slug: str = Field(min_length=1, description="Slug of the referenced skill.")
    version: int | None = Field(
        default=None,
        ge=1,
        description="Pinned skill version, or ``None`` to track the latest.",
    )


class AgentPresetSubagentRef(BaseModel):
    """Reference from an agent preset to another preset used as a subagent."""

    model_config = ConfigDict(extra="allow")

    slug: str = Field(
        min_length=1, description="Slug of the preset used as a subagent."
    )
    version: int | None = Field(
        default=None,
        ge=1,
        description="Pinned child preset version, or ``None`` to track the latest.",
    )
    name: str | None = Field(
        default=None, description="Optional runtime alias for the subagent."
    )
    description: str | None = Field(
        default=None, description="Optional runtime description for the subagent."
    )
    max_turns: int | None = Field(
        default=None,
        ge=1,
        description="Optional maximum turns for the subagent.",
    )


class AgentPresetVersionResourceSpec(BaseModel):
    """Immutable agent preset snapshot stored under a preset's versions dir."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["agent_preset_version"] = Field(
        default="agent_preset_version", description="Resource type discriminator."
    )
    version_number: int = Field(
        ge=1, description="Preset version number scoped to the parent preset."
    )
    name: str = Field(min_length=1, description="Human-readable preset name.")
    instructions: str | None = Field(
        default=None,
        description="System prompt / instructions for the agent.",
    )
    tool_approvals: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-tool approval policy keyed by tool name.",
    )
    actions: list[str] = Field(
        default_factory=list,
        description="Registry action names the agent may call.",
    )
    skills: list[AgentPresetSkillBinding] = Field(
        default_factory=list,
        description="Skills bound to the preset, optionally version-pinned.",
    )
    subagents: list[AgentPresetSubagentRef] = Field(
        default_factory=list,
        description="Other presets invoked as subagents.",
    )
    catalog_id: uuid.UUID | None = Field(
        default=None,
        description="Source model catalog entry id, if model is catalog-backed.",
    )
    model_name: str | None = Field(
        default=None,
        description="Override model name, or ``None`` to inherit defaults.",
    )
    model_provider: str | None = Field(
        default=None,
        description="Override model provider, or ``None`` to inherit defaults.",
    )
    base_url: str | None = Field(
        default=None,
        description="Override provider base URL, if any.",
    )
    output_type: Any | None = Field(
        default=None,
        description="Structured output schema, if the agent returns structured data.",
    )
    namespaces: list[str] = Field(
        default_factory=list,
        description="Registry namespaces the agent's tools are drawn from.",
    )
    mcp_integrations: list[str] = Field(
        default_factory=list,
        description="MCP integration slugs available to the agent.",
    )
    retries: int = Field(
        default=3,
        ge=0,
        description="Maximum agent run retries.",
    )
    enable_thinking: bool = Field(
        default=True,
        description="Whether extended thinking is enabled.",
    )
    enable_internet_access: bool = Field(
        default=False,
        description="Whether the agent may access the internet.",
    )


class AgentPresetResourceSpec(BaseModel):
    """Canonical Git-owned desired state for an agent preset."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["agent_preset"] = Field(
        default="agent_preset", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the preset's single-segment file path key.",
    )
    slug: str = Field(
        min_length=1, description="Unique preset slug used for cross-references."
    )
    name: str = Field(min_length=1, description="Human-readable preset name.")
    current_version: int | None = Field(
        default=None,
        ge=1,
        description="Current preset version, or ``None`` if unpublished.",
    )
    folder_path: str | None = Field(
        default=None, description="Workspace folder the preset lives under, if any."
    )
    tags: list[str] = Field(default_factory=list, description="Free-form preset tags.")
    instructions: str | None = Field(
        default=None,
        exclude=True,
        description="System prompt / instructions for the agent.",
    )
    tool_approvals: dict[str, Any] = Field(
        default_factory=dict,
        exclude=True,
        description="Per-tool approval policy keyed by tool name.",
    )
    actions: list[str] = Field(
        default_factory=list,
        exclude=True,
        description="Registry action names the agent may call.",
    )
    skills: list[AgentPresetSkillBinding] = Field(
        default_factory=list,
        exclude=True,
        description="Skills bound to the preset, optionally version-pinned.",
    )
    subagents: list[AgentPresetSubagentRef] = Field(
        default_factory=list,
        exclude=True,
        description="Other presets invoked as subagents.",
    )
    catalog_id: uuid.UUID | None = Field(
        default=None,
        exclude=True,
        description="Source model catalog entry id, if model is catalog-backed.",
    )
    model_name: str | None = Field(
        default=None,
        exclude=True,
        description="Override model name, or ``None`` to inherit defaults.",
    )
    model_provider: str | None = Field(
        default=None,
        exclude=True,
        description="Override model provider, or ``None`` to inherit defaults.",
    )
    base_url: str | None = Field(
        default=None,
        exclude=True,
        description="Override provider base URL, if any.",
    )
    output_type: Any | None = Field(
        default=None,
        exclude=True,
        description="Structured output schema, if the agent returns structured data.",
    )
    namespaces: list[str] = Field(
        default_factory=list,
        exclude=True,
        description="Registry namespaces the agent's tools are drawn from.",
    )
    mcp_integrations: list[str] = Field(
        default_factory=list,
        exclude=True,
        description="MCP integration slugs available to the agent.",
    )
    retries: int = Field(
        default=3,
        ge=0,
        exclude=True,
        description="Maximum agent run retries.",
    )
    enable_thinking: bool = Field(
        default=True,
        exclude=True,
        description="Whether extended thinking is enabled.",
    )
    enable_internet_access: bool = Field(
        default=False,
        exclude=True,
        description="Whether the agent may access the internet.",
    )
    versions: dict[int, AgentPresetVersionResourceSpec] = Field(
        default_factory=dict,
        exclude=True,
        description="In-memory preset version snapshots keyed by version number.",
    )


class SkillFileSpec(BaseModel):
    """One file belonging to a skill, identified by path and content hash."""

    model_config = ConfigDict(extra="allow")

    path: str = Field(min_length=1, description="Skill-relative file path.")
    sha256: str = Field(
        min_length=64,
        max_length=64,
        description="Hex-encoded SHA-256 of the file contents.",
    )


class SkillVersionResourceSpec(BaseModel):
    """Immutable skill snapshot stored under a skill's versions dir."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["skill_version"] = Field(
        default="skill_version", description="Resource type discriminator."
    )
    version_number: int = Field(
        ge=1, description="Skill version number scoped to the parent skill."
    )
    name: str = Field(min_length=1, description="Human-readable skill version name.")
    description: str | None = Field(
        default=None, description="Optional skill version description."
    )
    files: list[SkillFileSpec] = Field(
        default_factory=list,
        description="Manifest of the version's files and their content hashes.",
    )
    file_contents: dict[str, str] = Field(
        default_factory=dict,
        exclude=True,
        description="In-memory file contents keyed by path; excluded from serialization.",
    )


class SkillResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a published skill."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["skill"] = Field(
        default="skill", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the skill's single-segment file path key.",
    )
    slug: str = Field(
        min_length=1, description="Unique skill slug used for cross-references."
    )
    name: str = Field(min_length=1, description="Human-readable skill name.")
    description: str | None = Field(
        default=None, description="Optional skill description."
    )
    current_version: int | None = Field(
        default=None,
        ge=1,
        description="Published skill version, or ``None`` if unversioned.",
    )
    files: list[SkillFileSpec] = Field(
        default_factory=list,
        description="Manifest of the skill's files and their content hashes.",
    )
    file_contents: dict[str, str] = Field(
        default_factory=dict,
        exclude=True,
        description="In-memory file contents keyed by path; excluded from serialization.",
    )
    versions: dict[int, SkillVersionResourceSpec] = Field(
        default_factory=dict,
        exclude=True,
        description="In-memory skill version snapshots keyed by version number.",
    )


class TableColumnSpec(BaseModel):
    """Canonical Git-owned desired state for a single table column.

    Optional fields default to ``None`` so the YAML serializer (which omits
    null values) writes a minimal column definition: ``nullable`` appears only
    when the column is not nullable, and ``unique`` only when the column backs
    the table's unique index.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Column name.")
    type: str = Field(min_length=1, description="SQL column type, lowercased.")
    nullable: bool | None = Field(
        default=None,
        description="``False`` when the column is not nullable; null means nullable.",
    )
    default: Any = Field(default=None, description="Optional default value.")
    options: list[str] | None = Field(
        default=None, description="Allowed values for select-style columns."
    )
    unique: bool | None = Field(
        default=None,
        description="``True`` when this column backs the table's unique index.",
    )


class TableResourceSpec(BaseModel):
    """Canonical Git-owned desired state for table metadata and schema only."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["table"] = Field(
        default="table", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the table's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Table name.")
    columns: list[TableColumnSpec] = Field(
        default_factory=list, description="Column definitions for the table schema."
    )


class CaseTagResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a case tag."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["case_tag"] = Field(
        default="case_tag", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the case tag's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Case tag name.")
    color: str | None = Field(default=None, description="Optional display color.")


class CaseDropdownResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a case dropdown field."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["case_dropdown"] = Field(
        default="case_dropdown", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the dropdown's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Case dropdown field name.")
    options: list[dict[str, Any]] = Field(
        default_factory=list, description="Dropdown option definitions."
    )
    is_ordered: bool = Field(
        default=False, description="Whether option order is significant."
    )
    icon_name: str | None = Field(default=None, description="Optional icon name.")
    position: int = Field(default=0, description="Display ordering position.")
    required_on_closure: bool = Field(
        default=False,
        description="Whether a value is required before a case can close.",
    )


class CaseDurationAnchorSpec(BaseModel):
    """Event boundary describing one end of a case duration."""

    model_config = ConfigDict(extra="forbid")

    event: CaseEventType = Field(
        description="Case event type that marks this boundary."
    )
    selection: CaseDurationAnchorSelection = Field(
        default=CaseDurationAnchorSelection.FIRST,
        description="Which matching event to pick (first or last).",
    )
    timestamp_path: str = Field(
        default="created_at",
        description="Path to the timestamp field on the selected event.",
    )
    field_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Field equality filters narrowing matching events.",
    )


class CaseDurationResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a case duration metric."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["case_duration"] = Field(
        default="case_duration", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the duration's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Case duration metric name.")
    description: str | None = Field(
        default=None, description="Optional metric description."
    )
    start: CaseDurationAnchorSpec = Field(
        description="Event boundary that starts the duration."
    )
    end: CaseDurationAnchorSpec = Field(
        description="Event boundary that ends the duration."
    )


class CaseFieldResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a custom case field."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["case_field"] = Field(
        default="case_field", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the field's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Case field name.")
    field_type: str | None = Field(
        default=None, description="Underlying field data type, if specified."
    )
    kind: str | None = Field(
        default=None, description="Field kind/category, if specified."
    )
    options: list[str] | None = Field(
        default=None, description="Allowed values for enumerated fields, if any."
    )
    required_on_closure: bool = Field(
        default=False,
        description="Whether a value is required before a case can close.",
    )


class VariableResourceSpec(BaseModel):
    """Canonical Git-owned desired state for an environment-scoped variable.

    The ``value`` is excluded from serialization; Git tracks only the variable's
    metadata, not its material.
    """

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["variable"] = Field(
        default="variable", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the variable's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Variable name.")
    environment: str = Field(
        min_length=1, description="Environment the variable is scoped to."
    )
    keys: list[str] | None = Field(
        default=None, description="Key names contained in the variable, if structured."
    )
    description: str | None = Field(
        default=None, description="Optional variable description."
    )
    tags: list[str] = Field(
        default_factory=list, description="Free-form variable tags."
    )

    @model_validator(mode="before")
    @classmethod
    def reject_variable_values(cls, data: Any) -> Any:
        """Reject input carrying ``value``/``values`` so variables stay metadata-only."""
        if isinstance(data, dict) and ({"value", "values"} & data.keys()):
            raise ValueError("variable value material is not allowed in Git")
        return data


class SecretMetadataResourceSpec(BaseModel):
    """Canonical Git-owned desired state for a secret's metadata only.

    Tracks a secret's name, environment, type, key names, and tags. The secret
    value material itself is never stored in Git.
    """

    model_config = ConfigDict(extra="allow")

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    type: Literal["secret_metadata"] = Field(
        default="secret_metadata", description="Resource type discriminator."
    )
    id: str = Field(
        min_length=1,
        description="Stable source id; the secret's single-segment file path key.",
    )
    name: str = Field(min_length=1, description="Secret name.")
    environment: str = Field(
        min_length=1, description="Environment the secret is scoped to."
    )
    secret_type: str | None = Field(
        default=None, description="Secret type/category, if specified."
    )
    keys: list[str] = Field(
        default_factory=list,
        description="Key names contained in the secret (names only, no values).",
    )
    tags: list[str] = Field(default_factory=list, description="Free-form secret tags.")
    description: str | None = Field(
        default=None, description="Optional secret description."
    )

    @model_validator(mode="before")
    @classmethod
    def reject_secret_values(cls, data: Any) -> Any:
        """Reject input carrying ``value``/``values`` so secrets never reach Git."""
        if isinstance(data, dict) and ({"value", "values"} & data.keys()):
            raise ValueError("secret value material is not allowed in Git")
        return data


# Resource-map fields on :class:`WorkspaceSpec`, paired with their sync resource
# type and human label. Single source of truth for the validator and counter
# below, so adding a resource type means editing this table plus the field.
_RESOURCE_FIELDS: tuple[tuple[SyncResourceType, str, str], ...] = (
    (SyncResourceType.WORKFLOW, "workflows", "Workflow"),
    (SyncResourceType.AGENT_PRESET, "agent_presets", "Agent preset"),
    (SyncResourceType.SKILL, "skills", "Skill"),
    (SyncResourceType.TABLE, "tables", "Table"),
    (SyncResourceType.CASE_TAG, "case_tags", "Case tag"),
    (SyncResourceType.CASE_FIELD, "case_fields", "Case field"),
    (SyncResourceType.CASE_DROPDOWN, "case_dropdowns", "Case dropdown"),
    (SyncResourceType.CASE_DURATION, "case_durations", "Case duration"),
    (SyncResourceType.VARIABLE, "variables", "Variable"),
    (SyncResourceType.SECRET_METADATA, "secret_metadata", "Secret metadata"),
)


class WorkspaceSpec(BaseModel):
    """Full Git-owned desired state for a workspace across all resource types.

    Each resource type maps ``source_id`` to its spec model, mirroring the
    repository layout.
    """

    version: Literal[1] = Field(default=1, description="Spec schema version.")
    workflows: dict[str, WorkflowResourceSpec] = Field(
        default_factory=dict, description="Workflow specs keyed by source id."
    )
    agent_presets: dict[str, AgentPresetResourceSpec] = Field(
        default_factory=dict, description="Agent preset specs keyed by source id."
    )
    skills: dict[str, SkillResourceSpec] = Field(
        default_factory=dict, description="Skill specs keyed by source id."
    )
    tables: dict[str, TableResourceSpec] = Field(
        default_factory=dict, description="Table specs keyed by source id."
    )
    case_tags: dict[str, CaseTagResourceSpec] = Field(
        default_factory=dict, description="Case tag specs keyed by source id."
    )
    case_fields: dict[str, CaseFieldResourceSpec] = Field(
        default_factory=dict, description="Case field specs keyed by source id."
    )
    case_dropdowns: dict[str, CaseDropdownResourceSpec] = Field(
        default_factory=dict, description="Case dropdown specs keyed by source id."
    )
    case_durations: dict[str, CaseDurationResourceSpec] = Field(
        default_factory=dict, description="Case duration specs keyed by source id."
    )
    variables: dict[str, VariableResourceSpec] = Field(
        default_factory=dict, description="Variable specs keyed by source id."
    )
    secret_metadata: dict[str, SecretMetadataResourceSpec] = Field(
        default_factory=dict, description="Secret metadata specs keyed by source id."
    )

    @model_validator(mode="after")
    def validate_resource_keys(self) -> WorkspaceSpec:
        """Require each map key to equal the ``id`` of the spec it points to."""
        for _resource_type, attr, label in _RESOURCE_FIELDS:
            for source_id, spec in getattr(self, attr).items():
                if source_id != spec.id:
                    raise ValueError(
                        f"{label} map key {source_id!r} does not match "
                        f"spec id {spec.id!r}"
                    )
        return self

    def resource_count_map(self) -> dict[str, int]:
        """Count specs per resource type, keyed by :class:`SyncResourceType` value."""
        return {
            resource_type.value: len(getattr(self, attr))
            for resource_type, attr, _label in _RESOURCE_FIELDS
        }


class WorkspaceProjection(BaseModel):
    """Locally projected workspace state plus the files it serializes to."""

    manifest: WorkspaceManifest = Field(
        description="Manifest describing the projected repository layout."
    )
    spec: WorkspaceSpec = Field(description="Projected workspace desired state.")
    files: dict[str, str] = Field(
        description="Serialized repository files keyed by repository-relative path."
    )


class WorkspaceRemoteSnapshot(BaseModel):
    """Workspace state read back from a remote Git commit."""

    commit_sha: str = Field(description="SHA of the commit the snapshot was read from.")
    tree_sha: str | None = Field(
        default=None, description="SHA of the commit's tree, if known."
    )
    files: dict[str, str] = Field(
        description="Repository files keyed by repository-relative path."
    )
    spec: WorkspaceSpec = Field(
        description="Workspace desired state parsed from the commit."
    )


class ResourceRef(BaseModel):
    """Reference to a single resource by type and either source or local id."""

    resource_type: SyncResourceType = Field(
        description="Type of the referenced resource."
    )
    source_id: str | None = Field(
        default=None,
        description="Git source id of the resource, if referenced by source id.",
    )
    local_id: uuid.UUID | None = Field(
        default=None,
        description="Local database id of the resource, if referenced by local id.",
    )


class WorkspaceSyncExportRequest(BaseModel):
    """Request to commit selected workspace resources to a Git branch."""

    message: str = Field(min_length=1, description="Commit message for the export.")
    branch: str = Field(description="Target branch to commit to.")
    create_pr: bool = Field(
        default=False, description="Whether to open a pull request for the commit."
    )
    pr_base_branch: str | None = Field(
        default=None, description="Base branch for the pull request, if created."
    )
    resources: list[ResourceRef] | None = Field(
        default=None,
        description="Specific resources to export, or ``None`` to export all.",
    )
    provider: VcsProvider = Field(
        default=VcsProvider.GITHUB, description="VCS provider to push to."
    )
    include_schedules: bool = Field(
        default=False,
        description="Whether to include workflow schedules in the export.",
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        """Trim the commit message and reject empty or whitespace-only input."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be empty or whitespace")
        return cleaned


class WorkspaceSyncExportPreviewRequest(BaseModel):
    """Request a dry-run projection of what an export would push to Git."""

    resources: list[ResourceRef] | None = Field(
        default=None,
        description="Specific resources to preview, or ``None`` for all.",
    )
    include_schedules: bool = Field(
        default=False,
        description="Whether to include workflow schedules in the preview.",
    )
    compare_ref: str | None = Field(
        default=None,
        description=(
            "Repository ref to compare the projected export against. When omitted, "
            "the preview only returns the export manifest summary."
        ),
    )
    provider: VcsProvider = Field(
        default=VcsProvider.GITHUB,
        description="VCS provider to read the comparison ref from.",
    )


class WorkspaceSyncPreviewResource(BaseModel):
    """One resource included in a workspace sync export preview."""

    resource_type: SyncResourceType = Field(
        description="Type of resource included in the preview."
    )
    source_id: str = Field(description="Stable Git source id for the resource.")
    name: str = Field(description="Human-readable resource name.")
    path: str = Field(description="Primary repository path written for the resource.")


class WorkspaceSyncExportPreview(BaseModel):
    """Projection summary of the resources an export would commit.

    Mirrors the pull dry-run preview: it projects the selected resources
    locally without writing to Git or mutating sync mappings.
    """

    resource_counts: dict[str, int] = Field(
        description="Count of resources to commit, keyed by resource type."
    )
    files: list[str] = Field(
        description="Repository-relative paths the export would write."
    )
    resources: list[WorkspaceSyncPreviewResource] = Field(
        default_factory=list,
        description="Displayable resources included in the export preview.",
    )
    resource_diffs: list[PullResourceDiff] = Field(
        default_factory=list,
        description=(
            "Per-resource file diffs between the comparison ref and projected export."
        ),
    )


class WorkspaceSyncExportResult(BaseModel):
    """Outcome of a workspace export: the commit made and files written."""

    commit: CommitInfo = Field(description="Metadata for the commit that was created.")
    files: list[str] = Field(
        description="Repository-relative paths written by the export."
    )

    def as_workflow_publish_result(self) -> WorkflowDslPublishResult:
        """Adapt this export result to the legacy :class:`WorkflowDslPublishResult`."""
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
