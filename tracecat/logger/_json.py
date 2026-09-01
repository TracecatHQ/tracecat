"""Compact JSON serialization for collector-backed application logs."""

import json
import sys
import traceback
from datetime import UTC
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict

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

    return (
        json.dumps(
            payload,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            skipkeys=True,
        )
        + "\n"
    )


def write_json_log(message: LogMessage) -> None:
    """Write one serialized Loguru message to standard error."""
    sys.stderr.write(serialize_log_record(message.record))
