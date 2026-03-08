"""Loggers to override default FastAPI uvicorn logger behavior."""

import os
import sys
from typing import TYPE_CHECKING

import orjson
from loguru import logger as base_logger

from tracecat.logger.security import (
    build_log_payload,
    is_json_logging_enabled,
    maybe_warn_verbose_payload_logging_ignored,
    sanitize_log_record,
)

if TYPE_CHECKING:
    from loguru import Record


# Set to True by worker entrypoint to enable replay filtering
_is_worker_process = False


def _workflow_replay_filter(record: "Record") -> bool:
    """Filter that prevents logging during Temporal workflow replay.

    Only active when _is_worker_process is True (set by worker entrypoint).
    """
    if not _is_worker_process:
        return True

    try:
        from temporalio import workflow

        if workflow.unsafe.is_replaying():
            return False
    except Exception:
        pass

    return True


try:
    base_logger.remove(0)
except ValueError:
    pass
maybe_warn_verbose_payload_logging_ignored()


def _json_formatter(record: "Record") -> str:
    payload = build_log_payload(record)
    return orjson.dumps(payload).decode("utf-8") + "\n"


def _patch_record(record: "Record") -> None:
    sanitize_log_record(record)


logger = base_logger.patch(_patch_record)
logger.add(
    sink=sys.stderr,
    colorize=not is_json_logging_enabled(),
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format=_json_formatter
    if is_json_logging_enabled()
    else (
        "<fg #808080>{time:YYYY-MM-DD HH:mm:ss.SSSSSS}Z [{process}] |</fg #808080>"
        " <level>{level: <8}  <fg #808080>{name}:{function}:{line} -</fg #808080> {message}"
        " <fg #808080>|</fg #808080> {extra}</level>"
    ),
    filter=_workflow_replay_filter,
)
