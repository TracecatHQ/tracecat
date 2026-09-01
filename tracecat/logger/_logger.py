"""Loggers to override default FastAPI uvicorn logger behavior."""

import os
import sys
from typing import TYPE_CHECKING

from loguru import logger as base_logger
from opentelemetry import trace
from opentelemetry.trace import TraceFlags

from tracecat.logger.config import LogFormat, log_format_from_env

if TYPE_CHECKING:
    from loguru import Record


# Set to True by worker entrypoint to enable replay filtering
_is_worker_process = False

_CONSOLE_FORMAT = (
    "<fg #808080>{time:YYYY-MM-DD HH:mm:ss.SSSSSS}Z [{process}] |</fg #808080>"
    " <level>{level: <8}  <fg #808080>{name}:{function}:{line} -</fg #808080>"
    " {message} <fg #808080>|</fg #808080> {extra}</level>"
)


def _add_process_context(record: "Record") -> None:
    """Attach trusted process identity and active trace identifiers."""
    record["extra"]["process_service"] = os.environ.get(
        "TRACECAT__SERVICE_NAME", "tracecat"
    )
    record["extra"]["environment"] = os.environ.get("TRACECAT__APP_ENV", "development")
    _add_trace_context(record)


def _add_trace_context(record: "Record") -> None:
    """Add active OpenTelemetry identifiers to Loguru structured extras."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return
    record["extra"]["trace_id"] = f"{span_context.trace_id:032x}"
    record["extra"]["span_id"] = f"{span_context.span_id:016x}"
    record["extra"]["trace_sampled"] = bool(
        span_context.trace_flags & TraceFlags.SAMPLED
    )


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
log_format = log_format_from_env()
base_logger.configure(patcher=_add_process_context)
base_logger.add(
    sink=sys.stderr,
    colorize=log_format is LogFormat.CONSOLE,
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format=_CONSOLE_FORMAT,
    filter=_workflow_replay_filter,
    diagnose=False,
    serialize=log_format is LogFormat.JSON,
)

logger = base_logger
