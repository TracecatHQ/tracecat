from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from temporalio import workflow

from tracecat.logger import logger as process_logger


def _in_workflow_context() -> bool:
    """Return whether we're currently running in a Temporal workflow context."""
    if workflow.unsafe.in_sandbox():
        return True

    runtime_cls = getattr(workflow, "_Runtime", None)
    maybe_current = getattr(runtime_cls, "maybe_current", None)
    if maybe_current is None:
        return False

    try:
        return maybe_current() is not None
    except Exception:
        return False


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


def _format_fields(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    serialized = ", ".join(
        f"{key}={_safe_repr(value)}" for key, value in sorted(fields.items())
    )
    return f" | {serialized}"


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeLogger:
    """Logger adapter for workflow code paths.

    Uses Temporal's workflow logger when running in workflow context to avoid
    process-wide Loguru sink lock contention from workflow activation threads.
    Falls back to process logger outside workflow runtime (e.g. unit tests).
    """

    _bound_fields: dict[str, Any] = field(default_factory=dict)

    def bind(self, **fields: Any) -> WorkflowRuntimeLogger:
        return WorkflowRuntimeLogger({**self._bound_fields, **fields})

    def trace(self, message: str, **fields: Any) -> None:
        self._log("trace", message, **fields)

    def debug(self, message: str, **fields: Any) -> None:
        self._log("debug", message, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self._log("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._log("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._log("error", message, **fields)

    def _log(self, level: str, message: str, **fields: Any) -> None:
        merged_fields = {**self._bound_fields, **fields}
        formatted_message = f"{message}{_format_fields(merged_fields)}"

        if _in_workflow_context():
            temporal_level = "debug" if level == "trace" else level
            getattr(workflow.logger, temporal_level)(formatted_message)
            return

        getattr(process_logger, level)(formatted_message)


workflow_logger = WorkflowRuntimeLogger()


def get_workflow_logger(**fields: Any) -> WorkflowRuntimeLogger:
    return workflow_logger.bind(**fields)
