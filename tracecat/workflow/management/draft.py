"""Shared workflow draft edit-document engine (RFC 6902 JSON Patch).

This module is the transport-neutral engine for building, canonicalizing,
hashing, validating, and persisting the editable workflow "edit document" used
by ``edit_workflow``. It is shared by the MCP server (``tracecat.mcp.server``)
and internal FastAPI routers so neither has to import the other.

Transport neutrality: every recoverable error is raised as
:class:`WorkflowEditError`. Callers are responsible for mapping it onto their
transport's error type (``fastmcp.exceptions.ToolError`` for the MCP server,
``fastapi.HTTPException`` for routers), preserving the original message and any
structured ``code``/``details`` payload.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection, Iterator, Mapping, Sequence
from typing import Any, Literal, Protocol, cast

import orjson
from asyncpg import UniqueViolationError
from fastmcp.exceptions import ToolError
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.logger import AuditEventDetails, audit_log
from tracecat.auth.types import Role
from tracecat.db.common import DBConstraints
from tracecat.db.models import Action, Workflow
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    build_action_statements_from_actions,
)
from tracecat.dsl.schemas import DSLConfig
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.mcp.json_patch import validate_patch_paths
from tracecat.mcp.schemas import (
    JsonPatchOperation,
    WorkflowEditDefinition,
    WorkflowEditDocument,
    WorkflowEditMetadata,
    WorkflowEditRequest,
    WorkflowLayout,
    WorkflowSchedule,
)
from tracecat.validation.schemas import (
    ValidationDetail,
    ValidationDetailListTA,
    ValidationResult,
)
from tracecat.validation.service import validate_dsl
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerConfig,
    CaseTriggerRead,
    is_case_trigger_configured,
)
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate
from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleRead
from tracecat.workflow.schedules.service import WorkflowSchedulesService


class WorkflowEditError(Exception):
    """Transport-neutral error for the workflow edit-document engine.

    Callers map this onto their transport error type while preserving the
    message and structured payload. ``code``/``details`` reconstruct the
    JSON payloads that the MCP server previously embedded directly in
    ``ToolError`` (``{"type": "validation_error", ...}`` and
    ``{"type": "conflict", ...}``).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        conflict: bool = False,
        current_revision: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details
        self.conflict = conflict
        self.current_revision = current_revision


_WORKFLOW_EDITABLE_TOP_LEVEL_PATHS = frozenset(
    {"metadata", "definition", "layout", "schedules", "case_trigger"}
)
# First path segments that only ever live under ``/definition``. Models
# (especially weaker ones) routinely drop the ``/definition`` prefix and write
# ``/actions/0/...`` instead of ``/definition/actions/0/...``. Because these
# segments are unambiguous, we transparently rewrite them to the canonical path
# before validation so a near-miss patch still applies.
_WORKFLOW_DEFINITION_CHILD_SEGMENTS = frozenset(
    {"actions", "entrypoint", "config", "returns"}
)
_WORKFLOW_NONEDITABLE_PATH_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("definition", "config", "scheduler"),
    ("definition", "actions", "*", "id"),
)
_WORKFLOW_NONREMOVABLE_PATH_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("schedules", "*", "status"),
    ("case_trigger", "status"),
)


class _WorkflowEditDocumentSource(Protocol):
    title: str
    description: str | None
    status: str
    alias: str | None
    error_handler: str | None
    entrypoint: str | None
    expects: dict[str, Any] | None
    config: dict[str, Any] | None
    returns: Any | None
    trigger_position_x: float | None
    trigger_position_y: float | None
    viewport_x: float | None
    viewport_y: float | None
    viewport_zoom: float | None
    actions: list[Action] | None
    schedules: Sequence[Any] | None
    case_trigger: Any | None


# NOTE: `_validation_result_payload` is replicated here (rather than imported
# from `tracecat.mcp.server`) because that helper is a private of the MCP
# server and is reused by several non-edit MCP tools; importing a private from
# `tracecat.mcp` would couple this transport-neutral module back to the MCP
# transport. The body mirrors `tracecat.mcp.server._validation_result_payload`.
def _validation_result_payload(vr: ValidationResult) -> dict[str, object]:
    """Serialize a validation result for user-facing error output."""
    payload = cast(
        dict[str, object], vr.root.model_dump(mode="json", exclude_none=True)
    )
    if "msg" in payload and "message" not in payload:
        payload["message"] = payload["msg"]
    raw_detail = payload.get("detail")
    if isinstance(raw_detail, list):
        payload["details"] = [
            (
                {
                    "type": detail.get("type", ""),
                    "msg": detail.get("msg", str(detail)),
                    "loc": list(detail.get("loc", ()) or ()),
                }
                if isinstance(detail, dict)
                else {
                    "type": getattr(detail, "type", ""),
                    "msg": getattr(detail, "msg", str(detail)),
                    "loc": list(getattr(detail, "loc", ()) or ()),
                }
            )
            for detail in raw_detail
        ]
    return payload


# NOTE: `schedule_create_from_payload` is a public copy moved out of
# `tracecat.mcp.server` (where it was `_schedule_create_from_payload`). It was
# only used by the edit-document path, so it lives here and the MCP server
# imports it.
def schedule_create_from_payload(
    *,
    workflow_id: WorkflowUUID,
    schedule: WorkflowSchedule,
) -> ScheduleCreate:
    return ScheduleCreate(
        workflow_id=workflow_id,
        inputs=schedule.inputs,
        cron=schedule.cron,
        every=schedule.every,
        offset=schedule.offset,
        start_at=schedule.start_at,
        end_at=schedule.end_at,
        status=schedule.status,
        timeout=schedule.timeout,
    )


def _workflow_schedule_sort_key(schedule: Any) -> str:
    """Return a stable sort key for workflow schedules."""
    payload = ScheduleRead.model_validate(schedule, from_attributes=True).model_dump(
        mode="json",
        exclude={
            "id",
            "workspace_id",
            "workflow_id",
            "created_at",
            "updated_at",
        },
    )
    if payload["timeout"] is None:
        payload["timeout"] = 0
    return json.dumps(payload, sort_keys=True)


def build_workflow_edit_document(
    workflow: Workflow | _WorkflowEditDocumentSource,
) -> WorkflowEditDocument:
    """Build the canonical JSON document used by edit_workflow."""
    actions = sorted(
        workflow.actions or [],
        key=lambda action: action.ref,
    )
    action_statements = build_action_statements_from_actions(actions) if actions else []

    schedules = []
    for schedule in sorted(
        workflow.schedules or [],
        key=_workflow_schedule_sort_key,
    ):
        schedule_payload = ScheduleRead.model_validate(
            schedule, from_attributes=True
        ).model_dump(
            mode="json",
            exclude={
                "id",
                "workspace_id",
                "workflow_id",
                "created_at",
                "updated_at",
            },
        )
        if schedule_payload["timeout"] is None:
            schedule_payload["timeout"] = 0
        schedules.append(schedule_payload)

    case_trigger_payload: dict[str, Any] | None = None
    if case_trigger := workflow.case_trigger:
        case_trigger_read = CaseTriggerRead.model_validate(
            case_trigger, from_attributes=True
        )
        if is_case_trigger_configured(
            status=case_trigger_read.status,
            event_types=case_trigger_read.event_types,
            tag_filters=case_trigger_read.tag_filters,
        ):
            candidate_payload = case_trigger_read.model_dump(
                mode="json", exclude={"id", "workflow_id"}
            )
            try:
                case_trigger_payload = CaseTriggerConfig.model_validate(
                    candidate_payload
                ).model_dump(mode="json")
            except ValidationError:
                case_trigger_payload = None

    # ``DSLInput`` (the import/upload write path) accepts a title/description as
    # bare strings with no length bounds, but ``WorkflowEditMetadata`` (and the
    # other edit-document schemas) re-validate them with stricter constraints
    # (title 3-100 chars, description <=1000). A legacy/imported workflow whose
    # persisted metadata violates those bounds would make the constructors below
    # raise a raw Pydantic ``ValidationError``. Callers only map
    # ``WorkflowEditError`` onto their transport, so an unconverted error escapes
    # as an opaque 500 and the agent can neither read nor repair the workflow.
    # Convert it to a transport-neutral ``WorkflowEditError`` (-> 400/tool error)
    # carrying the offending field details so the agent can fix the metadata.
    try:
        return WorkflowEditDocument.model_validate(
            {
                "metadata": WorkflowEditMetadata(
                    title=workflow.title,
                    description=workflow.description or "",
                    status=cast(Literal["online", "offline"], workflow.status),
                    alias=workflow.alias,
                    error_handler=workflow.error_handler,
                ).model_dump(mode="json"),
                "definition": WorkflowEditDefinition(
                    entrypoint=DSLEntrypoint(
                        ref=workflow.entrypoint,
                        expects=workflow.expects,
                    ),
                    actions=action_statements,
                    config=DSLConfig.model_validate(workflow.config or {}),
                    returns=workflow.returns,
                ).model_dump(mode="json", exclude_none=False),
                "layout": {
                    "trigger": {
                        "x": (
                            workflow.trigger_position_x
                            if workflow.trigger_position_x is not None
                            else 0.0
                        ),
                        "y": (
                            workflow.trigger_position_y
                            if workflow.trigger_position_y is not None
                            else 0.0
                        ),
                    },
                    "viewport": {
                        "x": workflow.viewport_x
                        if workflow.viewport_x is not None
                        else 0.0,
                        "y": workflow.viewport_y
                        if workflow.viewport_y is not None
                        else 0.0,
                        "zoom": (
                            workflow.viewport_zoom
                            if workflow.viewport_zoom is not None
                            else 1.0
                        ),
                    },
                    "actions": [
                        {
                            "ref": action.ref,
                            "x": action.position_x,
                            "y": action.position_y,
                        }
                        for action in actions
                    ],
                },
                "schedules": schedules,
                "case_trigger": case_trigger_payload,
            }
        )
    except ValidationError as exc:
        details = ValidationDetail.list_from_pydantic(exc)
        message = (
            "This workflow's stored metadata is invalid for editing. Fix the "
            "reported field(s) (title must be 3-100 characters; description must "
            "be at most 1000 characters) and retry."
        )
        raise WorkflowEditError(
            message,
            code="validation_error",
            details={
                "type": "validation_error",
                "status": "error",
                "message": message,
                "errors": ValidationDetailListTA.dump_python(details, mode="json"),
            },
        ) from exc


def workflow_edit_document_payload(
    document: WorkflowEditDocument,
) -> dict[str, Any]:
    """Serialize the canonical workflow edit document for patching and hashing."""
    return document.model_dump(mode="json", exclude_none=False)


def _workflow_schedule_payload_sort_key(schedule: dict[str, Any]) -> str:
    """Return a stable sort key for already-serialized workflow schedules."""
    payload = dict(schedule)
    if payload["timeout"] is None:
        payload["timeout"] = 0
    return json.dumps(payload, sort_keys=True)


def canonicalize_workflow_edit_document(
    document: WorkflowEditDocument,
) -> WorkflowEditDocument:
    """Normalize document ordering before hashing or comparison."""
    payload = workflow_edit_document_payload(document)
    payload["definition"]["actions"] = sorted(
        payload["definition"]["actions"],
        key=lambda action: cast(str, action["ref"]),
    )
    payload["layout"]["actions"] = sorted(
        payload["layout"]["actions"],
        key=lambda action: cast(str, action["ref"]),
    )
    payload["schedules"] = sorted(
        payload["schedules"],
        key=_workflow_schedule_payload_sort_key,
    )
    return WorkflowEditDocument.model_validate(payload)


def normalize_workflow_edit_document_for_persisted_revision(
    document: WorkflowEditDocument,
) -> WorkflowEditDocument:
    """Normalize transient edit state that persistence drops on refresh."""
    payload = workflow_edit_document_payload(document)
    action_refs = [action.ref for action in document.definition.actions]
    action_ref_set = set(action_refs)
    layout_by_ref = {
        action_layout["ref"]: action_layout
        for action_layout in payload["layout"]["actions"]
        if action_layout["ref"] in action_ref_set
    }
    payload["layout"]["actions"] = [
        layout_by_ref.get(ref, {"ref": ref, "x": 0.0, "y": 0.0}) for ref in action_refs
    ]
    if payload["case_trigger"] is not None:
        case_trigger = CaseTriggerConfig.model_validate(payload["case_trigger"])
        if not case_trigger.is_configured():
            payload["case_trigger"] = None
    return WorkflowEditDocument.model_validate(payload)


def workflow_edit_document_changed_sections(
    original_document: WorkflowEditDocument,
    updated_document: WorkflowEditDocument,
) -> set[str]:
    original_payload = workflow_edit_document_payload(
        canonicalize_workflow_edit_document(original_document)
    )
    updated_payload = workflow_edit_document_payload(
        canonicalize_workflow_edit_document(updated_document)
    )
    return {
        key for key in updated_payload if updated_payload[key] != original_payload[key]
    }


def compute_workflow_edit_revision(document: WorkflowEditDocument) -> str:
    """Compute a stable draft revision for the editable workflow document."""
    payload = workflow_edit_document_payload(
        canonicalize_workflow_edit_document(document)
    )
    serialized = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(serialized).hexdigest()


def _decode_patch_path(path: str) -> tuple[str, ...]:
    """Decode a JSON pointer path into unescaped tokens."""
    return tuple(
        token.replace("~1", "/").replace("~0", "~") for token in path.split("/")[1:]
    )


def _encode_patch_path(tokens: tuple[str, ...]) -> str:
    """Encode path tokens into a JSON pointer."""
    return "/" + "/".join(
        token.replace("~", "~0").replace("/", "~1") for token in tokens
    )


def _patch_path_matches_pattern(path: str, pattern: tuple[str, ...]) -> bool:
    """Check whether a decoded patch path matches a non-editable pattern."""
    tokens = _decode_patch_path(path)
    if len(tokens) < len(pattern):
        return False
    return all(
        expected in {"*", actual}
        for actual, expected in zip(tokens, pattern, strict=False)
    )


def _iter_noneditable_payload_paths(
    payload: Any,
    pattern: tuple[str, ...],
    *,
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[str, ...]]:
    """Yield matching non-editable JSON pointer token paths present in a payload."""
    if not pattern:
        yield prefix
        return

    token, *rest = pattern
    if isinstance(payload, dict):
        if token == "*":
            for key, value in payload.items():
                yield from _iter_noneditable_payload_paths(
                    value,
                    tuple(rest),
                    prefix=prefix + (str(key),),
                )
        elif token in payload:
            yield from _iter_noneditable_payload_paths(
                payload[token],
                tuple(rest),
                prefix=prefix + (token,),
            )
    elif isinstance(payload, list):
        if token == "*":
            for index, value in enumerate(payload):
                yield from _iter_noneditable_payload_paths(
                    value,
                    tuple(rest),
                    prefix=prefix + (str(index),),
                )
        elif token.isdigit():
            index = int(token)
            if 0 <= index < len(payload):
                yield from _iter_noneditable_payload_paths(
                    payload[index],
                    tuple(rest),
                    prefix=prefix + (token,),
                )


def _iter_missing_payload_paths(
    payload: Any,
    pattern: tuple[str, ...],
    *,
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[str, ...]]:
    """Yield non-removable JSON pointer token paths missing from a payload."""
    if not pattern:
        return

    token, *rest = pattern
    if isinstance(payload, dict):
        if token == "*":
            for key, value in payload.items():
                yield from _iter_missing_payload_paths(
                    value,
                    tuple(rest),
                    prefix=prefix + (str(key),),
                )
        elif token in payload:
            yield from _iter_missing_payload_paths(
                payload[token],
                tuple(rest),
                prefix=prefix + (token,),
            )
        elif not rest:
            yield prefix + (token,)
    elif isinstance(payload, list):
        if token == "*":
            for index, value in enumerate(payload):
                yield from _iter_missing_payload_paths(
                    value,
                    tuple(rest),
                    prefix=prefix + (str(index),),
                )
        elif token.isdigit():
            index = int(token)
            if 0 <= index < len(payload):
                yield from _iter_missing_payload_paths(
                    payload[index],
                    tuple(rest),
                    prefix=prefix + (token,),
                )


def validate_workflow_patch_payload(payload: dict[str, Any]) -> WorkflowEditDocument:
    """Validate a patched payload and return the parsed edit document.

    Rejects payloads that still contain non-editable nested fields, then parses
    the payload into a :class:`WorkflowEditDocument`. A schema failure (e.g. an
    unknown field such as ``on_error`` that a model invented) is surfaced as a
    structured :class:`WorkflowEditError` so callers return a 400/tool error the
    agent can read and correct — never an opaque 500.
    """
    for pattern in _WORKFLOW_NONEDITABLE_PATH_PATTERNS:
        if found_path := next(_iter_noneditable_payload_paths(payload, pattern), None):
            raise WorkflowEditError(
                f"Patch path '{_encode_patch_path(found_path)}' is not editable via edit_workflow"
            )
    for pattern in _WORKFLOW_NONREMOVABLE_PATH_PATTERNS:
        if missing_path := next(_iter_missing_payload_paths(payload, pattern), None):
            raise WorkflowEditError(
                f"Patch path '{_encode_patch_path(missing_path)}' cannot be removed via edit_workflow"
            )
    try:
        return WorkflowEditDocument.model_validate(payload)
    except ValidationError as exc:
        details = ValidationDetail.list_from_pydantic(exc)
        message = (
            "The patched workflow document is invalid. Fix the reported "
            "field(s) and retry. Note: an action has no `on_error` field — to "
            "run a step when another step fails, add a dependency on the source "
            "ref's error path, e.g. `depends_on: ['<source_ref>.error']`."
        )
        raise WorkflowEditError(
            message,
            code="validation_error",
            details={
                "type": "validation_error",
                "status": "error",
                "message": message,
                "errors": ValidationDetailListTA.dump_python(details, mode="json"),
            },
        ) from exc
    except TracecatValidationError as exc:
        # Nested ActionStatement validators (e.g. interaction + for_each) raise
        # a raw TracecatValidationError, not a Pydantic ValidationError, so it
        # bypasses the branch above. Surface it as a structured WorkflowEditError
        # too so the caller gets a 400/tool error to correct, never a 500.
        message = str(exc) or "The patched workflow document is invalid."
        raise WorkflowEditError(
            message,
            code="validation_error",
            details={
                "type": "validation_error",
                "status": "error",
                "message": message,
            },
        ) from exc


def validate_workflow_patch_paths(patch_ops: list[JsonPatchOperation]) -> None:
    """Reject JSON Patch paths outside the editable workflow document."""
    try:
        validate_patch_paths(
            patch_ops,
            allowed_top_level_paths=_WORKFLOW_EDITABLE_TOP_LEVEL_PATHS,
        )
    except ToolError as exc:
        # Keep the engine transport-neutral: the json_patch helper raises the
        # MCP ToolError, which we normalize to WorkflowEditError here.
        raise WorkflowEditError(str(exc)) from exc
    for patch_op in patch_ops:
        for path in (patch_op.path, patch_op.from_):
            if path is None:
                continue
            if any(
                _patch_path_matches_pattern(path, pattern)
                for pattern in _WORKFLOW_NONEDITABLE_PATH_PATTERNS
            ):
                raise WorkflowEditError(
                    f"Patch path '{path}' is not editable via edit_workflow"
                )
        removed_paths: tuple[str | None, ...] = (
            (patch_op.path,)
            if patch_op.op == "remove"
            else (patch_op.from_,)
            if patch_op.op == "move"
            else ()
        )
        for path in removed_paths:
            if path is None:
                continue
            if any(
                _patch_path_matches_pattern(path, pattern)
                for pattern in _WORKFLOW_NONREMOVABLE_PATH_PATTERNS
            ):
                raise WorkflowEditError(
                    f"Patch path '{path}' cannot be removed via edit_workflow"
                )


def workflow_edit_document_to_dsl(document: WorkflowEditDocument) -> DSLInput:
    """Convert the editable workflow document into a DSLInput."""
    return DSLInput(
        title=document.metadata.title,
        description=document.metadata.description,
        entrypoint=document.definition.entrypoint,
        actions=document.definition.actions,
        config=document.definition.config,
        returns=document.definition.returns,
        error_handler=document.metadata.error_handler,
    )


def _raise_dsl_validation_edit_error(
    validation_results: Collection[ValidationResult],
) -> None:
    """Raise a transport-neutral validation error mirroring the MCP payload.

    The ``details`` payload reproduces the dict that
    ``tracecat.mcp.server._raise_dsl_validation_tool_error`` previously
    JSON-encoded into a ``ToolError``.
    """
    if validation_results:
        raise WorkflowEditError(
            f"{len(validation_results)} validation error(s)",
            code="validation_error",
            details={
                "type": "validation_error",
                "message": f"{len(validation_results)} validation error(s)",
                "status": "error",
                "errors": [
                    _validation_result_payload(result) for result in validation_results
                ],
            },
        )


async def validate_workflow_edit_document(
    document: WorkflowEditDocument,
    *,
    workflow_id: WorkflowUUID,
    existing_layout_action_refs: set[str] | None = None,
    validate_definition: bool = False,
    changed_sections: Collection[str] | None = None,
    session: AsyncSession | None = None,
    role: Role | None = None,
) -> None:
    """Validate editable workflow document semantics before persistence.

    Runs the same correctness checks the persist path enforces so a
    ``validate_only`` dry run reports the same outcome a real apply would.
    ``changed_sections`` (computed by callers) gates the DB-stateful checks --
    alias uniqueness and case-trigger online-readiness -- so they only run when
    that section actually changed.
    """
    action_refs = {action.ref for action in document.definition.actions}
    allowed_layout_action_refs = action_refs | (existing_layout_action_refs or set())
    for action_layout in document.layout.actions:
        if action_layout.ref not in allowed_layout_action_refs:
            raise WorkflowEditError(
                f"Unknown action ref {action_layout.ref!r} in layout.actions"
            )
    if document.definition.actions:
        try:
            dsl = workflow_edit_document_to_dsl(document)
        except (TracecatValidationError, ValidationError, ValueError) as exc:
            raise WorkflowEditError(f"Invalid workflow definition: {exc}") from exc
        if validate_definition:
            if session is None or role is None:
                raise RuntimeError("session and role are required for DSL validation")
            validation_results = await validate_dsl(
                session=session,
                dsl=dsl,
                role=role,
            )
            _raise_dsl_validation_edit_error(validation_results)
    for schedule in document.schedules:
        try:
            schedule_create_from_payload(
                workflow_id=workflow_id,
                schedule=schedule,
            )
        except ValidationError as exc:
            raise WorkflowEditError(f"Invalid workflow schedule: {exc}") from exc

    sections = set(changed_sections or ())

    # Alias uniqueness is enforced by a DB unique constraint that the persist
    # path would otherwise surface as a 500. Pre-check here so a dry run and a
    # real apply both report a normal alias conflict as a recoverable error.
    if "metadata" in sections and document.metadata.alias is not None:
        if session is None or role is None:
            raise RuntimeError("session and role are required for alias validation")
        if role.workspace_id is None:
            raise RuntimeError("role.workspace_id is required for alias validation")
        await _raise_if_alias_taken(
            session=session,
            workspace_id=role.workspace_id,
            workflow_id=workflow_id,
            alias=document.metadata.alias,
        )

    # Case-trigger online-readiness is a DB-stateful check (needs a published,
    # runnable definition). The persist path runs it inside the case-trigger
    # service; mirror it here so validate_only can't report ``valid: true`` for a
    # config that fails on apply. An inert (unconfigured) config has nothing to
    # validate and is dropped by ``normalize_workflow_edit_document_for_persisted_revision``
    # on persist, so skip the service construction (and its role requirement)
    # entirely when there is nothing to validate.
    if (
        "case_trigger" in sections
        and document.case_trigger is not None
        and document.case_trigger.is_configured()
    ):
        if session is None or role is None:
            raise RuntimeError(
                "session and role are required for case-trigger validation"
            )
        case_trigger_service = CaseTriggersService(session, role=role)
        try:
            await case_trigger_service.validate_case_trigger_config(
                workflow_id, document.case_trigger
            )
        except TracecatValidationError as exc:
            raise WorkflowEditError(f"Invalid case trigger: {exc}") from exc


async def _raise_if_alias_taken(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    workflow_id: WorkflowUUID,
    alias: str,
) -> None:
    """Raise ``WorkflowEditError`` if another workflow already owns ``alias``.

    Mirrors the unique constraint ``uq_workflow_alias_workspace_id`` as an
    application-level pre-check so the conflict is recoverable (and visible to
    ``validate_only``) instead of bubbling out of the commit as a 500.
    """
    existing_id = await session.scalar(
        select(Workflow.id).where(
            Workflow.workspace_id == workspace_id,
            Workflow.alias == alias,
            Workflow.id != workflow_id,
        )
    )
    if existing_id is not None:
        raise WorkflowEditError(DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE.msg())


def _normalize_patch_pointer(path: str | None) -> str | None:
    """Rewrite a near-miss JSON pointer to its canonical editable path.

    Adds the missing ``/definition`` prefix when the first segment is a
    definition-only key (``actions``/``entrypoint``/``config``/``returns``).
    Leaves already-correct and unrelated paths untouched.
    """
    if path is None or not path.startswith("/"):
        return path
    first, sep, rest = path[1:].partition("/")
    if first in _WORKFLOW_DEFINITION_CHILD_SEGMENTS:
        return f"/definition/{first}{sep}{rest}" if sep else f"/definition/{first}"
    return path


def _normalize_patch_op_paths(
    patch_ops: list[JsonPatchOperation],
) -> list[JsonPatchOperation]:
    """Return patch ops with near-miss ``path``/``from`` pointers canonicalized."""
    normalized: list[JsonPatchOperation] = []
    for op in patch_ops:
        new_path = _normalize_patch_pointer(op.path)
        new_from = _normalize_patch_pointer(op.from_)
        if new_path == op.path and new_from == op.from_:
            normalized.append(op)
        else:
            normalized.append(
                op.model_copy(update={"path": new_path, "from_": new_from})
            )
    return normalized


def parse_workflow_edit_request(
    *,
    base_revision: str,
    patch_ops: list[dict[str, Any]] | list[JsonPatchOperation],
    validate_only: bool,
) -> WorkflowEditRequest:
    """Parse and validate the edit_workflow request payload.

    Near-miss patch paths (e.g. ``/actions/0/...`` missing the ``/definition``
    prefix) are transparently canonicalized before validation so a model that
    drops the prefix still succeeds.
    """
    request = WorkflowEditRequest.model_validate(
        {
            "base_revision": base_revision,
            "patch_ops": patch_ops,
            "validate_only": validate_only,
        }
    )
    request.patch_ops = _normalize_patch_op_paths(request.patch_ops)
    validate_workflow_patch_paths(request.patch_ops)
    return request


def _workflow_edit_audit_details(
    *,
    role: Any,
    service: WorkflowsManagementService,
    workflow: Workflow,
    original_document: WorkflowEditDocument,
    updated_document: WorkflowEditDocument,
    changed_sections: set[str] | None = None,
) -> AuditEventDetails:
    if changed_sections is None:
        changed_sections = workflow_edit_document_changed_sections(
            original_document,
            updated_document,
        )
    return AuditEventDetails(
        resource_id=WorkflowUUID.new(workflow.id),
        data={"changed_fields": sorted(changed_sections)},
        emit=bool(changed_sections),
    )


@audit_log(
    resource_type="workflow",
    action="update",
    attempt_metadata=_workflow_edit_audit_details,
)
async def persist_workflow_edit_document(
    *,
    role: Any,
    service: WorkflowsManagementService,
    workflow: Workflow,
    original_document: WorkflowEditDocument,
    updated_document: WorkflowEditDocument,
    changed_sections: set[str] | None = None,
) -> None:
    """Persist changes from the editable workflow document back to the draft."""
    if changed_sections is None:
        changed_sections = workflow_edit_document_changed_sections(
            original_document,
            updated_document,
        )
    if not changed_sections:
        return

    workflow_id = WorkflowUUID.new(workflow.id)
    layout_payload = updated_document.layout.model_dump(mode="json", exclude_none=False)
    _, _, action_positions = extract_layout_positions(layout_payload)

    if "definition" in changed_sections:
        # The action graph is being rewritten. Bump graph_version so a builder
        # holding a stale base_version gets a 409 from the graph API instead of
        # silently applying graph operations against the old action graph. The
        # workflow row is held under FOR UPDATE for the lifetime of this session,
        # so this increment cannot race a concurrent graph mutation.
        workflow.graph_version += 1
        if updated_document.definition.actions:
            await replace_workflow_definition_from_dsl(
                service=service,
                workflow=workflow,
                dsl=workflow_edit_document_to_dsl(updated_document),
                action_positions=action_positions,
            )
        else:
            workflow.title = updated_document.metadata.title
            workflow.description = updated_document.metadata.description
            workflow.status = updated_document.metadata.status
            workflow.alias = updated_document.metadata.alias
            workflow.error_handler = updated_document.metadata.error_handler
            workflow.entrypoint = updated_document.definition.entrypoint.ref
            entrypoint_data = updated_document.definition.entrypoint.model_dump(
                exclude_none=True
            )
            workflow.expects = entrypoint_data.get("expects") or {}
            workflow.returns = updated_document.definition.returns
            workflow.config = updated_document.definition.config.model_dump(mode="json")
            service.session.add(workflow)
            await service.session.execute(
                delete(Action).where(
                    Action.workspace_id == service.workspace_id,
                    Action.workflow_id == workflow.id,
                )
            )
            await service.session.flush()
            await service.session.refresh(workflow, ["actions"])
    if "metadata" in changed_sections:
        metadata = updated_document.metadata
        update_params = WorkflowUpdate(
            title=metadata.title,
            description=metadata.description,
            status=metadata.status,
            alias=metadata.alias,
            error_handler=metadata.error_handler,
        )
        for key, value in update_params.model_dump(exclude_unset=True).items():
            setattr(workflow, key, value)
        service.session.add(workflow)

    if "layout" in changed_sections:
        await service.session.refresh(workflow, ["actions"])
        allowed_missing_layout_action_refs = {
            layout_action.ref
            for layout_action in original_document.layout.actions
            if layout_action.ref
            not in {action.ref for action in updated_document.definition.actions}
        }
        apply_layout_to_workflow(
            workflow=workflow,
            layout=WorkflowLayout.model_validate(layout_payload),
            clear_missing=True,
            allowed_missing_action_refs=allowed_missing_layout_action_refs,
        )
        service.session.add(workflow)
        for action in workflow.actions:
            service.session.add(action)

    if "schedules" in changed_sections:
        schedule_service = WorkflowSchedulesService(service.session, role=role)
        await replace_workflow_schedules(
            service=schedule_service,
            workflow_id=workflow_id,
            schedules=updated_document.schedules,
        )
        # The deleted Schedule rows are already flushed, but the eagerly-loaded
        # workflow.schedules collection still references them; expire it so the
        # final commit/refresh and any save-update cascade do not touch deleted
        # instances.
        service.session.expire(workflow, ["schedules"])

    if "case_trigger" in changed_sections:
        case_trigger_service = CaseTriggersService(service.session, role=role)
        case_trigger_config = updated_document.case_trigger or CaseTriggerConfig(
            status="offline", event_types=[], tag_filters=[]
        )
        try:
            await case_trigger_service.upsert_case_trigger(
                workflow_id,
                case_trigger_config,
                create_missing_tags=True,
                commit=False,
            )
        except (TracecatValidationError, TracecatNotFoundError) as exc:
            raise WorkflowEditError(f"Invalid case trigger: {exc}") from exc

    try:
        await service.session.commit()
    except IntegrityError as exc:
        # Pre-validation catches alias conflicts under normal conditions, but a
        # concurrent edit can still claim the alias between the check and this
        # commit. Map the unique violation to a recoverable WorkflowEditError so
        # the conflict surfaces as a 400/409 instead of a raw 500.
        await service.session.rollback()
        cause: BaseException = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        if isinstance(
            cause, UniqueViolationError
        ) and DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE in str(cause):
            raise WorkflowEditError(
                DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE.msg()
            ) from exc
        raise WorkflowEditError("Workflow already exists") from exc
    await service.session.refresh(workflow)
    if any(section in changed_sections for section in {"definition", "layout"}):
        await service.session.refresh(workflow, ["actions"])
    if "schedules" in changed_sections:
        await service.session.refresh(workflow, ["schedules"])
    if "case_trigger" in changed_sections:
        await service.session.refresh(workflow, ["case_trigger"])


async def replace_workflow_definition_from_dsl(
    service: WorkflowsManagementService,
    workflow: Workflow,
    dsl: DSLInput,
    action_positions: dict[str, tuple[float, float]] | None = None,
) -> None:
    """Replace draft workflow definition from DSL (actions + metadata)."""
    workflow.title = dsl.title
    workflow.description = dsl.description
    workflow.entrypoint = dsl.entrypoint.ref
    entrypoint_data = dsl.entrypoint.model_dump()
    workflow.expects = entrypoint_data.get("expects") or {}
    workflow.returns = dsl.returns
    workflow.config = dsl.config.model_dump(mode="json")
    workflow.error_handler = dsl.error_handler
    service.session.add(workflow)

    await service.session.execute(
        delete(Action).where(
            Action.workspace_id == service.workspace_id,
            Action.workflow_id == workflow.id,
        )
    )
    await service.create_actions_from_dsl(dsl, workflow.id, action_positions)
    await service.session.flush()
    await service.session.refresh(workflow, ["actions"])


def extract_layout_positions(
    layout_data: WorkflowLayout | Mapping[str, object] | None,
) -> tuple[
    tuple[float, float] | None,
    tuple[float, float, float] | None,
    dict[str, tuple[float, float]] | None,
]:
    """Extract layout data into position tuples for workflow/action creation.

    Returns (trigger_position, viewport, action_positions).
    """
    if not layout_data:
        return None, None, None
    layout = (
        layout_data
        if isinstance(layout_data, WorkflowLayout)
        else WorkflowLayout.model_validate(layout_data)
    )
    trigger_position: tuple[float, float] | None = None
    if layout.trigger is not None:
        trigger_position = (
            layout.trigger.x if layout.trigger.x is not None else 0.0,
            layout.trigger.y if layout.trigger.y is not None else 0.0,
        )
    viewport: tuple[float, float, float] | None = None
    if layout.viewport is not None:
        viewport = (
            layout.viewport.x if layout.viewport.x is not None else 0.0,
            layout.viewport.y if layout.viewport.y is not None else 0.0,
            layout.viewport.zoom if layout.viewport.zoom is not None else 1.0,
        )
    action_positions: dict[str, tuple[float, float]] | None = None
    if layout.actions:
        action_positions = {
            ap.ref: (
                ap.x if ap.x is not None else 0.0,
                ap.y if ap.y is not None else 0.0,
            )
            for ap in layout.actions
        }
    return trigger_position, viewport, action_positions


def apply_layout_to_workflow(
    *,
    workflow: Workflow,
    layout: WorkflowLayout,
    clear_missing: bool = False,
    allowed_missing_action_refs: set[str] | None = None,
) -> None:
    """Apply optional trigger/action/viewport layout updates to a workflow."""
    if layout.trigger is not None:
        if clear_missing or layout.trigger.x is not None:
            workflow.trigger_position_x = (
                layout.trigger.x if layout.trigger.x is not None else 0.0
            )
        if clear_missing or layout.trigger.y is not None:
            workflow.trigger_position_y = (
                layout.trigger.y if layout.trigger.y is not None else 0.0
            )
    elif clear_missing:
        workflow.trigger_position_x = 0.0
        workflow.trigger_position_y = 0.0

    if layout.viewport is not None:
        if clear_missing or layout.viewport.x is not None:
            workflow.viewport_x = (
                layout.viewport.x if layout.viewport.x is not None else 0.0
            )
        if clear_missing or layout.viewport.y is not None:
            workflow.viewport_y = (
                layout.viewport.y if layout.viewport.y is not None else 0.0
            )
        if clear_missing or layout.viewport.zoom is not None:
            workflow.viewport_zoom = (
                layout.viewport.zoom if layout.viewport.zoom is not None else 1.0
            )
    elif clear_missing:
        workflow.viewport_x = 0.0
        workflow.viewport_y = 0.0
        workflow.viewport_zoom = 1.0

    action_by_ref = {action.ref: action for action in workflow.actions}
    seen_action_refs: set[str] = set()
    for action_position in layout.actions:
        action = action_by_ref.get(action_position.ref)
        if action is None:
            if (
                allowed_missing_action_refs is not None
                and action_position.ref in allowed_missing_action_refs
            ):
                continue
            raise WorkflowEditError(
                f"Unknown action ref {action_position.ref!r} in layout.actions"
            )
        seen_action_refs.add(action_position.ref)
        if clear_missing or action_position.x is not None:
            action.position_x = (
                action_position.x if action_position.x is not None else 0.0
            )
        if clear_missing or action_position.y is not None:
            action.position_y = (
                action_position.y if action_position.y is not None else 0.0
            )

    if clear_missing:
        missing_action_refs = set(action_by_ref) - seen_action_refs
        for action_ref in missing_action_refs:
            action = action_by_ref[action_ref]
            action.position_x = 0.0
            action.position_y = 0.0


async def replace_workflow_schedules(
    *,
    service: WorkflowSchedulesService,
    workflow_id: WorkflowUUID,
    schedules: Sequence[WorkflowSchedule],
) -> None:
    """Replace all schedules for a workflow from YAML payload.

    Delegates to the ``workflow:update``-scoped ``replace_schedules`` surface so
    editing schedules through the edit-workflow document path is gated by the
    workflow-edit permission rather than the standalone, admin-only
    ``schedule:delete`` scope.
    """
    await service.replace_schedules(
        workflow_id,
        [
            schedule_create_from_payload(
                workflow_id=workflow_id,
                schedule=schedule,
            )
            for schedule in schedules
        ],
        commit=False,
    )
