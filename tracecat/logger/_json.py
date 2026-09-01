"""Compact JSON serialization for collector-backed application logs."""

import json
import math
import sys
import traceback
from datetime import UTC
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


def _serialize_exception(record: "Record") -> LogException | None:
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


def _fallback_value(value: object) -> object:
    """Make uncommon Python values safe for the standard JSON fallback."""
    if isinstance(value, dict):
        return {str(key): _fallback_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_fallback_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _encode_payload(payload: StructuredLog) -> str:
    """Encode the common path quickly and preserve uncommon values on fallback."""
    try:
        return orjson.dumps(
            payload,
            default=str,
            option=orjson.OPT_APPEND_NEWLINE | orjson.OPT_NON_STR_KEYS,
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


def serialize_log_record(record: "Record") -> str:
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
    if exception := _serialize_exception(record):
        payload["exception"] = exception

    return _encode_payload(payload)


def write_json_log(message: LogMessage) -> None:
    """Write one serialized Loguru message to standard error."""
    sys.stderr.write(serialize_log_record(message.record))
