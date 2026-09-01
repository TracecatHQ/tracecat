"""Compact JSON serialization for collector-backed application logs."""

import json
import math
import sys
import traceback
from datetime import UTC, date, time
from enum import Enum
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict

import orjson

if TYPE_CHECKING:
    from loguru import Record

LOG_SCHEMA = "tracecat.log.v1"


class LogException(TypedDict):
    type: str
    message: str
    stack: str


class StructuredLog(TypedDict):
    schema: str
    timestamp: str
    level: str
    message: str
    service: str
    environment: str
    module: str
    function: str
    line: int
    attributes: dict[str, object]
    exception: NotRequired[LogException]


class LogMessage(Protocol):
    @property
    def record(self) -> "Record": ...


def _serialize_exception(
    record: "Record", formatted_message: str | None
) -> LogException | None:
    exception = record["exception"]
    if exception is None or exception.type is None or exception.value is None:
        return None

    stack = "".join(
        traceback.format_exception(
            exception.type,
            exception.value,
            exception.traceback,
        )
    )
    if formatted_message is not None and formatted_message.startswith(
        record["message"]
    ):
        formatted_stack = formatted_message[len(record["message"]) :].lstrip("\n")
        if formatted_stack:
            stack = formatted_stack

    return {
        "type": exception.type.__name__,
        "message": str(exception.value),
        "stack": stack,
    }


def _fallback_value(value: object, ancestors: set[int] | None = None) -> object:
    """Make uncommon Python values safe for the standard JSON fallback."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _fallback_value(value.value, ancestors)
    if isinstance(value, set | frozenset):
        return str(value)
    if not isinstance(value, dict | list | tuple):
        return str(value)

    if ancestors is None:
        ancestors = set()
    marker = id(value)
    if marker in ancestors:
        return "<recursive>"

    ancestors.add(marker)
    try:
        if isinstance(value, dict):
            return {
                _fallback_key(key): _fallback_value(item, ancestors)
                for key, item in value.items()
            }
        return [_fallback_value(item, ancestors) for item in value]
    finally:
        ancestors.remove(marker)


def _fallback_key(value: object) -> str:
    """Match orjson's supported non-string key encoding."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Enum):
        return _fallback_key(value.value)
    if isinstance(value, date | time):
        return value.isoformat()
    return str(value)


def _encode_payload(payload: StructuredLog) -> str:
    """Encode the common path quickly and preserve uncommon values on fallback."""
    try:
        return orjson.dumps(
            payload,
            default=str,
            option=(
                orjson.OPT_APPEND_NEWLINE
                | orjson.OPT_NON_STR_KEYS
                | orjson.OPT_PASSTHROUGH_DATACLASS
                | orjson.OPT_PASSTHROUGH_DATETIME
                | orjson.OPT_PASSTHROUGH_SUBCLASS
            ),
        ).decode()
    except TypeError:
        return (
            json.dumps(
                _fallback_value(payload),
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n"
        )


def serialize_log_record(record: "Record", formatted_message: str | None = None) -> str:
    """Serialize one Loguru record into the collector-facing schema."""
    attributes: dict[str, object] = dict(record["extra"])
    service = attributes.pop("process_service", "tracecat")
    environment = attributes.pop("environment", "development")

    payload: StructuredLog = {
        "schema": LOG_SCHEMA,
        "timestamp": record["time"].astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "level": record["level"].name,
        "message": record["message"],
        "service": str(service),
        "environment": str(environment),
        "module": record["name"] or "<unknown>",
        "function": record["function"],
        "line": record["line"],
        "attributes": attributes,
    }
    if exception := _serialize_exception(record, formatted_message):
        payload["exception"] = exception

    return _encode_payload(payload)


def write_json_log(message: LogMessage) -> None:
    """Write one serialized Loguru message to standard error."""
    sys.stderr.write(serialize_log_record(message.record, str(message)))
