"""Compact JSON serialization for collector-backed application logs."""

import sys
import traceback
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict
from uuid import UUID

import orjson

if TYPE_CHECKING:
    from loguru import Record


LOG_SCHEMA = "tracecat.log.v1"


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class LogSource(TypedDict):
    """Source location for one application log."""

    module: str
    function: str
    line: int


class LogException(TypedDict):
    """Sanitized exception details retained for warning and error diagnosis."""

    type: str
    message: str
    stack: str


class StructuredLog(TypedDict):
    """Versioned, allowlisted schema emitted to collector-backed deployments."""

    schema: str
    timestamp: str
    level: str
    message: str
    service: str
    environment: str
    source: LogSource
    event: NotRequired[str]
    owner: NotRequired[str]
    kind: NotRequired[str]
    retry_disposition: NotRequired[str]
    workflow_type: NotRequired[str]
    trace_id: NotRequired[str]
    span_id: NotRequired[str]
    trace_sampled: NotRequired[bool]
    workflow_id: NotRequired[str]
    execution_id: NotRequired[str]
    action_ref: NotRequired[str]
    error_type: NotRequired[str]
    attributes: NotRequired[dict[str, JsonValue]]
    exception: NotRequired[LogException]


class LogMessage(Protocol):
    """The portion of Loguru's runtime message contract used by the JSON sink."""

    @property
    def record(self) -> "Record": ...


_STRING_EXTRA_KEYS = frozenset(
    {
        "action_ref",
        "cause_type",
        "environment",
        "error_kind",
        "error_owner",
        "error_type",
        "event",
        "execution_id",
        "kind",
        "owner",
        "process_service",
        "retry_disposition",
        "span_id",
        "task_ref",
        "trace_id",
        "wf_exec_id",
        "wf_id",
        "workflow_execution_id",
        "workflow_id",
        "workflow_type",
    }
)


def _as_string(value: object) -> str | None:
    """Normalize identifier-like scalar values without serializing arbitrary objects."""
    if isinstance(value, str):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        enum_value = value.value
        if isinstance(enum_value, str):
            return enum_value
    return None


def _to_json_value(value: object) -> JsonValue:
    """Normalize intentional log attributes without exposing Loguru internals."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if -(1 << 63) <= value <= (1 << 64) - 1:
            return value
        return str(value)
    if value is None or isinstance(value, str | float):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date | Path):
        return str(value)
    if isinstance(value, Enum):
        return _to_json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence | Set) and not isinstance(value, bytes):
        return [_to_json_value(item) for item in value]
    return str(value)


def _first_string(extra: Mapping[str, object], *keys: str) -> str | None:
    """Return the first supported identifier from equivalent legacy field names."""
    for key in keys:
        if value := _as_string(extra.get(key)):
            return value
    return None


def _serialize_exception(record: "Record") -> LogException | None:
    """Render a traceback without Loguru's optional local-variable diagnostics."""
    exception = record["exception"]
    if exception is None or exception.type is None or exception.value is None:
        return None
    return {
        "type": exception.type.__name__,
        "message": str(exception.value),
        "stack": "".join(
            traceback.format_exception(
                exception.type,
                exception.value,
                exception.traceback,
            )
        ),
    }


def serialize_log_record(record: "Record") -> bytes:
    """Serialize one Loguru record into the Tracecat collector schema."""
    extra: Mapping[str, object] = record["extra"]
    payload: StructuredLog = {
        "schema": LOG_SCHEMA,
        "timestamp": record["time"].astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "level": record["level"].name,
        "message": record["message"],
        "service": _as_string(extra.get("process_service")) or "tracecat",
        "environment": _as_string(extra.get("environment")) or "development",
        "source": {
            "module": record["name"] or "<unknown>",
            "function": record["function"],
            "line": record["line"],
        },
    }

    if event := _as_string(extra.get("event")):
        payload["event"] = event
    if owner := _first_string(extra, "owner", "error_owner"):
        payload["owner"] = owner
    if kind := _first_string(extra, "kind", "error_kind"):
        payload["kind"] = kind
    if retry_disposition := _as_string(extra.get("retry_disposition")):
        payload["retry_disposition"] = retry_disposition
    if workflow_type := _as_string(extra.get("workflow_type")):
        payload["workflow_type"] = workflow_type
    if trace_id := _as_string(extra.get("trace_id")):
        payload["trace_id"] = trace_id
    if span_id := _as_string(extra.get("span_id")):
        payload["span_id"] = span_id
    if isinstance(trace_sampled := extra.get("trace_sampled"), bool):
        payload["trace_sampled"] = trace_sampled
    if workflow_id := _first_string(extra, "workflow_id", "wf_id"):
        payload["workflow_id"] = workflow_id
    if execution_id := _first_string(
        extra,
        "execution_id",
        "wf_exec_id",
        "workflow_execution_id",
    ):
        payload["execution_id"] = execution_id
    if action_ref := _first_string(extra, "action_ref", "task_ref"):
        payload["action_ref"] = action_ref
    if error_type := _first_string(extra, "error_type", "cause_type"):
        payload["error_type"] = error_type

    promoted_extra_keys = {
        key for key in _STRING_EXTRA_KEYS if _as_string(extra.get(key)) is not None
    }
    if isinstance(extra.get("trace_sampled"), bool):
        promoted_extra_keys.add("trace_sampled")

    attributes = {
        key: _to_json_value(value)
        for key, value in sorted(extra.items())
        if key not in promoted_extra_keys
    }
    if attributes:
        payload["attributes"] = attributes

    if exception := _serialize_exception(record):
        payload["exception"] = exception

    return orjson.dumps(payload, option=orjson.OPT_APPEND_NEWLINE)


def write_json_log(message: LogMessage) -> None:
    """Write one structured record to the process stderr stream."""
    sys.stderr.write(serialize_log_record(message.record).decode())
