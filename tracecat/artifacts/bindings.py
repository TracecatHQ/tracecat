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

from tracecat.artifacts.schemas import Artifact, ArtifactAdapter, ArtifactOp
from tracecat.cases.enums import CaseSeverity, CaseStatus

type RunStatus = Literal["running", "success", "failed", "cancelled"]


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
class ArtifactSideEffect:
    """Artifact operation derived from an action result."""

    op: ArtifactOp
    artifact: Artifact


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactProjectionContext:
    """Action result context used to derive artifact operations."""

    tool_name: str | None
    tool_input: Mapping[str, Any] | None
    tool_output: Any
    is_error: bool
    tool_call_id: str | None


type ArtifactBuilder = Callable[[ArtifactProjectionContext], Artifact | None]


@dataclass(frozen=True, kw_only=True, slots=True)
class ArtifactBinding:
    """Binding from canonical action names to artifact projection logic."""

    tool_names: tuple[str, ...]
    op: ArtifactOp
    build: ArtifactBuilder


def _build_case_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _case_artifact_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_deleted_case_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _deleted_case_artifact(ctx.tool_input, ctx.tool_call_id)


def _build_table_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _table_artifact_from_output(ctx.tool_output, ctx.tool_call_id)


def _build_workflow_run_artifact(ctx: ArtifactProjectionContext) -> Artifact | None:
    return _run_artifact_from_output(ctx.tool_output, ctx.tool_input, ctx.tool_call_id)


ARTIFACT_BINDINGS: tuple[ArtifactBinding, ...] = (
    ArtifactBinding(
        tool_names=("core.cases.create_case", "core.cases.update_case"),
        op="upsert",
        build=_build_case_artifact,
    ),
    ArtifactBinding(
        tool_names=("core.cases.delete_case",),
        op="remove",
        build=_build_deleted_case_artifact,
    ),
    ArtifactBinding(
        tool_names=("core.table.create_table",),
        op="upsert",
        build=_build_table_artifact,
    ),
    ArtifactBinding(
        tool_names=("core.workflow.execute", "core.workflow.get_status"),
        op="upsert",
        build=_build_workflow_run_artifact,
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

    if artifact := binding.build(ctx):
        yield ArtifactSideEffect(op=binding.op, artifact=artifact)


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
    try:
        decoded = orjson.loads(text)
    except orjson.JSONDecodeError:
        return None
    if isinstance(decoded, dict):
        return cast(Mapping[str, Any], decoded)
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
