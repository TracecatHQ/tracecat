"""Artifact bindings for known action result shapes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, NotRequired, Self, TypedDict, cast

import orjson
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from tracecat.artifacts.schemas import (
    Artifact,
    ArtifactAdapter,
    ArtifactOp,
    ArtifactSchema,
    ArtifactType,
)
from tracecat.cases.enums import CaseSeverity, CaseStatus

type RunStatus = Literal["running", "success", "failed", "cancelled"]
type ArtifactIdentityRefKind = Literal["id", "name"]


class CaseArtifactPayload(TypedDict):
    """Raw payload used to validate a case artifact."""

    type: Literal["case"]
    id: str
    title: str
    severity: CaseSeverity
    status: CaseStatus


class TableArtifactPayload(TypedDict):
    """Raw payload used to validate a table artifact."""

    type: Literal["table"]
    id: str
    title: str
    rowCount: NotRequired[int]


class AgentArtifactPayload(TypedDict):
    """Raw payload used to validate an agent preset artifact."""

    type: Literal["agent"]
    id: str
    title: str


class WorkflowArtifactPayload(TypedDict):
    """Raw payload used to validate a workflow artifact."""

    type: Literal["workflow"]
    id: str
    title: str
    color: str
    isPublished: NotRequired[bool]


class RunArtifactPayload(TypedDict):
    """Raw payload used to validate a workflow run artifact."""

    type: Literal["run"]
    id: str
    title: str
    workflowId: str
    status: RunStatus
    startedAt: str | datetime


class _ArtifactProjectionModel(BaseModel):
    model_config = ConfigDict(
        coerce_numbers_to_str=True, extra="ignore", populate_by_name=True
    )

    @classmethod
    def try_validate(cls, value: Mapping[str, Any]) -> Self | None:
        try:
            return cls.model_validate(value)
        except ValidationError:
            return None


class _CaseToolResult(_ArtifactProjectionModel):
    id: str = Field(validation_alias=AliasChoices("id", "case_id", "caseId"))
    title: str | None = Field(
        default=None, validation_alias=AliasChoices("summary", "title", "name")
    )
    severity: CaseSeverity = CaseSeverity.UNKNOWN
    status: CaseStatus = CaseStatus.UNKNOWN


class _CaseDeleteToolInput(_ArtifactProjectionModel):
    case_id: str = Field(validation_alias=AliasChoices("case_id", "caseId", "id"))


class _TableToolResult(_ArtifactProjectionModel):
    id: str = Field(validation_alias=AliasChoices("id", "table_id", "tableId"))
    title: str | None = Field(
        default=None, validation_alias=AliasChoices("name", "title")
    )
    row_count: int | None = Field(
        default=None,
        validation_alias=AliasChoices("row_count", "rowCount", "total_estimate"),
    )


class _TableMutationToolInput(_ArtifactProjectionModel):
    table: str = Field(
        validation_alias=AliasChoices("table", "name", "table_id", "tableId")
    )


class _TableIdToolInput(_ArtifactProjectionModel):
    table_id: str = Field(validation_alias=AliasChoices("table_id", "tableId"))


class _AgentPresetToolResult(_ArtifactProjectionModel):
    id: str
    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("name", "title"),
    )


class _WorkflowToolResult(_ArtifactProjectionModel):
    id: str = Field(
        validation_alias=AliasChoices("id", "workflow_id", "workflowId", "wf_id")
    )
    title: str | None = Field(
        default=None, validation_alias=AliasChoices("title", "name")
    )
    is_published: bool | None = Field(
        default=None, validation_alias=AliasChoices("is_published", "isPublished")
    )


class _WorkflowRunToolResult(_ArtifactProjectionModel):
    run_id: str = Field(
        validation_alias=AliasChoices("wf_exec_id", "wfExecId", "run_id", "id")
    )
    workflow_id: str = Field(
        validation_alias=AliasChoices("wf_id", "workflowId", "workflow_id")
    )
    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "workflow_title", "workflowTitle"),
    )
    status: str | None = None
    started_at: str | datetime | None = Field(
        default=None, validation_alias=AliasChoices("started_at", "startedAt")
    )


class _WorkflowRunToolInput(_ArtifactProjectionModel):
    workflow_alias: str | None = Field(
        default=None, validation_alias=AliasChoices("workflow_alias", "workflowAlias")
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactIdentityRef:
    """Unresolved reference to the domain object backing an artifact."""

    artifact_type: ArtifactType
    ref: str
    ref_kind: ArtifactIdentityRefKind


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactSideEffect:
    """Artifact operation derived from an action result."""

    op: ArtifactOp
    artifact: Artifact
    identity_ref: ArtifactIdentityRef | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactProjectionContext:
    """Action result context used to derive artifact operations."""

    tool_name: str | None
    tool_input: Mapping[str, Any] | None
    tool_output: Any
    is_error: bool
    tool_call_id: str | None


type ArtifactBuilder = Callable[
    [ArtifactProjectionContext], Artifact | Iterable[Artifact] | None
]
type ArtifactIdentityBuilder = Callable[
    [ArtifactProjectionContext],
    ArtifactIdentityRef | None,
]


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactBinding:
    """Binding from canonical action names to artifact projection logic."""

    tool_names: tuple[str, ...]
    op: ArtifactOp
    build: ArtifactBuilder
    identity: ArtifactIdentityBuilder | None = None


def _build_case_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _case_artifact_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_case_artifacts(ctx: ArtifactProjectionContext) -> Iterable[Artifact]:
    return _case_artifacts_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_deleted_case_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _deleted_case_artifact(ctx.tool_input, ctx.tool_call_id)


def _build_table_mutation_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    artifact = _table_artifact_from_output(ctx.tool_output, ctx.tool_call_id)
    if artifact is not None:
        return artifact
    return _table_artifact_from_input(ctx.tool_input, ctx.tool_call_id)


def _build_table_input_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    artifact = _table_artifact_from_input(ctx.tool_input, ctx.tool_call_id)
    if artifact is not None:
        return artifact
    return _table_artifact_from_output(ctx.tool_output, ctx.tool_call_id)


def _table_identity_from_input(
    ctx: ArtifactProjectionContext,
) -> ArtifactIdentityRef | None:
    return _table_identity_ref_from_input(ctx.tool_input)


def _table_identity_from_input_when_output_missing(
    ctx: ArtifactProjectionContext,
) -> ArtifactIdentityRef | None:
    if _table_artifact_from_output(ctx.tool_output, ctx.tool_call_id) is not None:
        return None
    return _table_identity_ref_from_input(ctx.tool_input)


def _build_table_artifacts(ctx: ArtifactProjectionContext) -> Iterable[Artifact]:
    return _table_artifacts_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_agent_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _agent_artifact_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_agent_artifacts(ctx: ArtifactProjectionContext) -> Iterable[Artifact]:
    return _agent_artifacts_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_workflow_run_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _run_artifact_from_output(ctx.tool_output, ctx.tool_input, ctx.tool_call_id)


def _build_workflow_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _workflow_artifact_from_output(ctx.tool_output, ctx.tool_call_id)


ARTIFACT_BINDINGS: tuple[ArtifactBinding, ...] = (
    ArtifactBinding(
        tool_names=(
            "core.cases.create_case",
            "core.cases.update_case",
            "core.cases.get_case",
        ),
        op="upsert",
        build=_build_case_artifact,
    ),
    ArtifactBinding(
        tool_names=("core.cases.list_cases", "core.cases.search_cases"),
        op="upsert",
        build=_build_case_artifacts,
    ),
    ArtifactBinding(
        tool_names=("core.cases.delete_case",),
        op="remove",
        build=_build_deleted_case_artifact,
    ),
    ArtifactBinding(
        tool_names=(
            "core.table.create_table",
            "core.table.get_table_metadata",
            "core.table.update_table",
            "core.table.create_column",
            "core.table.update_column",
            "core.table.delete_column",
        ),
        op="upsert",
        build=_build_table_mutation_artifact,
        identity=_table_identity_from_input_when_output_missing,
    ),
    ArtifactBinding(
        tool_names=("core.table.list_tables",),
        op="upsert",
        build=_build_table_artifacts,
    ),
    ArtifactBinding(
        tool_names=(
            "core.table.lookup",
            "core.table.lookup_many",
            "core.table.is_in",
            "core.table.search_rows",
            "core.table.insert_row",
            "core.table.insert_rows",
            "core.table.update_row",
            "core.table.delete_row",
            "core.table.download",
        ),
        op="upsert",
        build=_build_table_input_artifact,
        identity=_table_identity_from_input,
    ),
    ArtifactBinding(
        tool_names=(
            "ai.agent.create_preset",
            "ai.agent.get_preset",
            "ai.agent.update_preset",
        ),
        op="upsert",
        build=_build_agent_artifact,
    ),
    ArtifactBinding(
        tool_names=("ai.agent.list_presets",),
        op="upsert",
        build=_build_agent_artifacts,
    ),
    ArtifactBinding(
        tool_names=("core.workflow.execute", "core.workflow.get_status"),
        op="upsert",
        build=_build_workflow_run_artifact,
    ),
    ArtifactBinding(
        tool_names=("core.workflow.create_workflow",),
        op="upsert",
        build=_build_workflow_artifact,
    ),
)


def _index_artifact_bindings(
    bindings: Iterable[ArtifactBinding],
) -> dict[str, ArtifactBinding]:
    indexed: dict[str, ArtifactBinding] = {}
    for binding in bindings:
        for tool_name in binding.tool_names:
            if tool_name in indexed:
                raise ValueError(f"Duplicate artifact binding for tool {tool_name}")
            indexed[tool_name] = binding
    return indexed


_ARTIFACT_BINDINGS_BY_TOOL_NAME = _index_artifact_bindings(ARTIFACT_BINDINGS)


def _artifact_tuple(
    artifacts: Artifact | Iterable[Artifact] | None,
) -> tuple[Artifact, ...]:
    match artifacts:
        case None:
            return ()
        case ArtifactSchema():
            return (artifacts,)
        case _:
            return tuple(artifacts)


def artifact_side_effects_for_tool_result(
    *,
    tool_name: str | None,
    tool_input: Mapping[str, Any] | None,
    tool_output: Any,
    is_error: bool,
    tool_call_id: str | None,
) -> Iterator[ArtifactSideEffect]:
    """Derive artifact operations from known action result shapes."""
    ctx = ArtifactProjectionContext(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        is_error=is_error,
        tool_call_id=tool_call_id,
    )
    if ctx.is_error:
        return

    explicit_effects = list(
        _iter_explicit_artifact_side_effects(ctx.tool_output, ctx.tool_call_id)
    )
    if explicit_effects:
        yield from explicit_effects
        return

    if ctx.tool_name is None:
        return

    binding = _ARTIFACT_BINDINGS_BY_TOOL_NAME.get(ctx.tool_name)
    if binding is None:
        return

    artifacts = _artifact_tuple(binding.build(ctx))
    if not artifacts:
        return

    identity_ref = binding.identity(ctx) if binding.identity else None
    for artifact in artifacts:
        yield ArtifactSideEffect(
            op=binding.op,
            artifact=artifact,
            identity_ref=identity_ref,
        )


_EXPLICIT_ARTIFACT_SOURCES: tuple[tuple[str, ArtifactOp], ...] = (
    ("artifact", "upsert"),
    ("artifacts", "upsert"),
    ("removed_artifact", "remove"),
    ("removed_artifacts", "remove"),
    ("deleted_artifacts", "remove"),
)


def _iter_explicit_artifact_side_effects(
    value: Any, tool_call_id: str | None
) -> Iterator[ArtifactSideEffect]:
    data = _mapping_from_tool_output(value)
    if data is None:
        return

    for key, op in _EXPLICIT_ARTIFACT_SOURCES:
        source = data if key == "artifact" and "op" in data else data.get(key)
        for raw_item in _iter_artifact_items(source):
            if effect := _explicit_effect_from_value(raw_item, op, tool_call_id):
                yield effect


def _iter_artifact_items(value: Any) -> Iterable[Any]:
    match value:
        case None:
            return ()
        case list() as items:
            return items
        case tuple() as items:
            return items
        case _:
            return (value,)


def _explicit_effect_from_value(
    value: Any, default_op: ArtifactOp, tool_call_id: str | None
) -> ArtifactSideEffect | None:
    op = default_op
    raw_artifact = value
    if isinstance(value, Mapping) and "artifact" in value:
        raw_op = value.get("op", default_op)
        if raw_op != "upsert" and raw_op != "remove":
            return None
        op = raw_op
        raw_artifact = value["artifact"]

    artifact = _artifact_from_raw(raw_artifact, tool_call_id)
    if artifact is None:
        return None
    return ArtifactSideEffect(op=op, artifact=artifact)


def _artifact_from_raw(value: Any, tool_call_id: str | None) -> Artifact | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return ArtifactAdapter.validate_python(_with_parent_scope(value, tool_call_id))
    except Exception:
        return None


def _case_artifact_from_output(value: Any, tool_call_id: str | None) -> Artifact | None:
    data = _mapping_from_tool_output(value)
    if data is None:
        return None

    result = _CaseToolResult.try_validate(data)
    if result is None:
        return None

    payload: CaseArtifactPayload = {
        "type": "case",
        "id": result.id,
        "title": result.title or result.id,
        "severity": result.severity,
        "status": result.status,
    }
    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _case_artifacts_from_output(
    value: Any, tool_call_id: str | None
) -> Iterator[Artifact]:
    for data in _iter_mappings_from_tool_output(value):
        if result := _CaseToolResult.try_validate(data):
            payload: CaseArtifactPayload = {
                "type": "case",
                "id": result.id,
                "title": result.title or result.id,
                "severity": result.severity,
                "status": result.status,
            }
            yield ArtifactAdapter.validate_python(
                _with_parent_scope(payload, tool_call_id)
            )


def _deleted_case_artifact(
    tool_input: Mapping[str, Any] | None, tool_call_id: str | None
) -> Artifact | None:
    if tool_input is None:
        return None

    result = _CaseDeleteToolInput.try_validate(tool_input)
    if result is None:
        return None

    payload: CaseArtifactPayload = {
        "type": "case",
        "id": result.case_id,
        "title": result.case_id,
        "severity": CaseSeverity.UNKNOWN,
        "status": CaseStatus.UNKNOWN,
    }
    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _table_artifact_from_output(
    value: Any, tool_call_id: str | None
) -> Artifact | None:
    data = _mapping_from_tool_output(value)
    if data is None:
        return None

    result = _TableToolResult.try_validate(data)
    if result is None:
        return None

    payload: TableArtifactPayload = {
        "type": "table",
        "id": result.id,
        "title": result.title or result.id,
    }
    if result.row_count is not None:
        payload["rowCount"] = result.row_count

    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _table_artifacts_from_output(
    value: Any, tool_call_id: str | None
) -> Iterator[Artifact]:
    for data in _iter_mappings_from_tool_output(value):
        if result := _TableToolResult.try_validate(data):
            payload: TableArtifactPayload = {
                "type": "table",
                "id": result.id,
                "title": result.title or result.id,
            }
            if result.row_count is not None:
                payload["rowCount"] = result.row_count
            yield ArtifactAdapter.validate_python(
                _with_parent_scope(payload, tool_call_id)
            )


def _table_artifact_from_input(
    value: Mapping[str, Any] | None, tool_call_id: str | None
) -> Artifact | None:
    if value is None:
        return None

    result = _TableMutationToolInput.try_validate(value)
    if result is None:
        return None

    payload: TableArtifactPayload = {
        "type": "table",
        "id": result.table,
        "title": result.table,
    }
    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _table_identity_ref_from_input(
    value: Mapping[str, Any] | None,
) -> ArtifactIdentityRef | None:
    if value is None:
        return None

    if table_id := _TableIdToolInput.try_validate(value):
        return ArtifactIdentityRef(
            artifact_type="table",
            ref=table_id.table_id,
            ref_kind="id",
        )

    if table := _TableMutationToolInput.try_validate(value):
        return ArtifactIdentityRef(
            artifact_type="table",
            ref=table.table,
            ref_kind="name",
        )
    return None


def _agent_artifact_from_output(
    value: Any, tool_call_id: str | None
) -> Artifact | None:
    data = _mapping_from_tool_output(value)
    if data is None:
        return None

    result = _AgentPresetToolResult.try_validate(data)
    if result is None:
        return None

    payload: AgentArtifactPayload = {
        "type": "agent",
        "id": result.id,
        "title": result.title or result.id,
    }

    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _agent_artifacts_from_output(
    value: Any, tool_call_id: str | None
) -> Iterator[Artifact]:
    for data in _iter_mappings_from_tool_output(value):
        if result := _AgentPresetToolResult.try_validate(data):
            payload: AgentArtifactPayload = {
                "type": "agent",
                "id": result.id,
                "title": result.title or result.id,
            }
            yield ArtifactAdapter.validate_python(
                _with_parent_scope(payload, tool_call_id)
            )


# Default swatch color for workflow artifacts created via the chat copilot.
# Workflows have no inherent color; the UI uses this for the artifact tab swatch.
_DEFAULT_WORKFLOW_ARTIFACT_COLOR = "#6E56CF"


def _workflow_artifact_from_output(
    value: Any, tool_call_id: str | None
) -> Artifact | None:
    data = _mapping_from_tool_output(value)
    if data is None:
        return None

    result = _WorkflowToolResult.try_validate(data)
    if result is None:
        return None

    payload: WorkflowArtifactPayload = {
        "type": "workflow",
        "id": result.id,
        "title": result.title or result.id,
        "color": _DEFAULT_WORKFLOW_ARTIFACT_COLOR,
    }
    if result.is_published is not None:
        payload["isPublished"] = result.is_published

    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _run_artifact_from_output(
    value: Any,
    tool_input: Mapping[str, Any] | None,
    tool_call_id: str | None,
) -> Artifact | None:
    data = _mapping_from_tool_output(value)
    if data is None:
        return None

    result = _WorkflowRunToolResult.try_validate(data)
    if result is None:
        return None

    title = result.title
    if title is None and tool_input is not None:
        tool_input_result = _WorkflowRunToolInput.try_validate(tool_input)
        if tool_input_result is not None:
            title = tool_input_result.workflow_alias

    payload: RunArtifactPayload = {
        "type": "run",
        "id": result.run_id,
        "title": title or "Workflow run",
        "workflowId": result.workflow_id,
        "status": _run_status(result.status),
        "startedAt": result.started_at or datetime.now(UTC),
    }
    return ArtifactAdapter.validate_python(_with_parent_scope(payload, tool_call_id))


def _mapping_from_tool_output(value: Any) -> Mapping[str, Any] | None:
    match value:
        case Mapping() as mapping:
            if content := mapping.get("content"):
                if inner := _mapping_from_content_blocks(content):
                    return inner
            return cast(Mapping[str, Any], mapping)
        case str() as text:
            return _mapping_from_json_text(text)
        case list() as items:
            return _mapping_from_content_blocks(items)
        case tuple() as items:
            return _mapping_from_content_blocks(items)
        case _:
            return None


def _iter_mappings_from_tool_output(value: Any) -> Iterator[Mapping[str, Any]]:
    match value:
        case Mapping() as mapping:
            if content := mapping.get("content"):
                yield from _iter_mappings_from_tool_output(content)
                return

            text = mapping.get("text")
            if isinstance(text, str):
                if decoded := _json_value_from_text(text):
                    yield from _iter_mappings_from_tool_output(decoded)
                    return

            if items := mapping.get("items"):
                yield from _iter_mappings_from_tool_output(items)
                return

            yield cast(Mapping[str, Any], mapping)
        case str() as text:
            if decoded := _json_value_from_text(text):
                yield from _iter_mappings_from_tool_output(decoded)
        case list() as items:
            for item in items:
                yield from _iter_mappings_from_tool_output(item)
        case tuple() as items:
            for item in items:
                yield from _iter_mappings_from_tool_output(item)
        case _:
            model_dump = getattr(value, "model_dump", None)
            if callable(model_dump):
                try:
                    dumped = model_dump()
                except Exception:
                    return
                yield from _iter_mappings_from_tool_output(dumped)


def _mapping_from_content_blocks(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return _mapping_from_content_block(value) or cast(Mapping[str, Any], value)

    if not isinstance(value, Iterable) or isinstance(value, str | bytes):
        return None

    for item in value:
        if data := _mapping_from_content_block(item):
            return data
    return None


def _mapping_from_content_block(item: Any) -> Mapping[str, Any] | None:
    match item:
        case Mapping() as mapping:
            text = mapping.get("text")
            if isinstance(text, str):
                if data := _mapping_from_json_text(text):
                    return data
            if "content" in mapping:
                if data := _mapping_from_tool_output(mapping["content"]):
                    return data
        case str() as text:
            if data := _mapping_from_json_text(text):
                return data

    text = getattr(item, "text", None)
    if isinstance(text, str):
        if data := _mapping_from_json_text(text):
            return data

    content = getattr(item, "content", None)
    if content is not None:
        if data := _mapping_from_tool_output(content):
            return data

    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            return None
        if isinstance(dumped, Mapping):
            return _mapping_from_content_block(dumped)
    return None


def _mapping_from_json_text(text: str) -> Mapping[str, Any] | None:
    decoded = _json_value_from_text(text)
    if isinstance(decoded, dict):
        return cast(Mapping[str, Any], decoded)
    return None


def _json_value_from_text(text: str) -> Any | None:
    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        return None


def _with_parent_scope(
    artifact: Mapping[str, Any], tool_call_id: str | None
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(artifact)
    if tool_call_id is None:
        return payload

    raw_scope = payload.get("scope")
    scope: dict[str, Any] = dict(raw_scope) if isinstance(raw_scope, Mapping) else {}
    scope.setdefault("parentToolCallId", tool_call_id)
    payload["scope"] = scope
    return payload


def _run_status(value: Any) -> RunStatus:
    if not isinstance(value, str):
        return "running"

    match value.lower():
        case "running" | "started" | "pending":
            return "running"
        case "success" | "succeeded" | "completed":
            return "success"
        case "cancelled" | "canceled":
            return "cancelled"
        case "failed" | "error" | "terminated" | "timed_out" | "timeout":
            return "failed"
        case _:
            return "running"
