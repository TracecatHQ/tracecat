"""Loggers to override default FastAPI uvicorn logger behavior."""

import os
import sys
from typing import TYPE_CHECKING

from loguru import logger as base_logger

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
base_logger.add(
    sink=sys.stderr,
    colorize=True,
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="<fg #808080>{time:YYYY-MM-DD HH:mm:ss.SSSSSS}Z [{process}] |</fg #808080>"
    " <level>{level: <8}  <fg #808080>{name}:{function}:{line} -</fg #808080> {message}"
    " <fg #808080>|</fg #808080> {extra}</level>",
    filter=_workflow_replay_filter,
)

logger = base_logger
