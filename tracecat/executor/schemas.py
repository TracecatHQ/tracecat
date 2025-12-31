from __future__ import annotations

import json
import traceback
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import UUID4, BaseModel, Field

from tracecat import config
from tracecat.config import TRACECAT__APP_ENV
from tracecat.logger import logger


class ExecutorResultSuccess(BaseModel):
    """Successful executor result."""

    type: Literal["success"] = "success"
    result: Any


class ExecutorResultFailure(BaseModel):
    """Failed executor result."""

    type: Literal["failure"] = "failure"
    error: ExecutorActionErrorInfo


ExecutorResult = Annotated[
    ExecutorResultSuccess | ExecutorResultFailure,
    Field(discriminator="type"),
]


class ExecutorBackendType(StrEnum):
    """Executor backend types for action execution.

    - SANDBOXED_POOL: Warm nsjail workers (single-tenant, trusted, high throughput)
    - EPHEMERAL: Cold nsjail subprocess per action (multitenant, untrusted, full isolation)
    - DIRECT: In-process execution (TESTING ONLY - no isolation, no subprocess overhead)
    - AUTO: Auto-select based on environment
    """

    SANDBOXED_POOL = "sandboxed_pool"
    EPHEMERAL = "ephemeral"
    DIRECT = "direct"
    AUTO = "auto"

    @property
    def is_untrusted(self) -> bool:
        """Return True if this backend uses untrusted mode (no DB creds in sandbox)."""
        return self == ExecutorBackendType.EPHEMERAL


def _is_nsjail_available() -> bool:
    """Check if nsjail is available in the current environment.

    Validates that nsjail_path is an executable file and rootfs_path is a directory.
    """
    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    return nsjail_path.is_file() and rootfs_path.is_dir()


def _resolve_backend_type() -> ExecutorBackendType:
    """Resolve the backend type from config, handling 'auto' mode."""

    try:
        backend_type = ExecutorBackendType(config.TRACECAT__EXECUTOR_BACKEND)
    except ValueError:
        valid = ", ".join(f"'{v.value}'" for v in ExecutorBackendType)
        raise ValueError(
            f"Invalid TRACECAT__EXECUTOR_BACKEND: {config.TRACECAT__EXECUTOR_BACKEND!r}. "
            f"Valid values: {valid}"
        ) from None

    if backend_type == ExecutorBackendType.AUTO:
        # Auto-select based on environment
        if config.TRACECAT__DISABLE_NSJAIL:
            logger.info(
                "Auto-selecting 'direct' backend (DISABLE_NSJAIL=true)",
            )
            backend_type = ExecutorBackendType.DIRECT
        elif _is_nsjail_available():
            logger.info(
                "Auto-selecting 'sandboxed_pool' backend (nsjail available)",
            )
            backend_type = ExecutorBackendType.SANDBOXED_POOL
        else:
            logger.warning(
                "Auto-selecting 'direct' backend (nsjail not available)",
            )
            backend_type = ExecutorBackendType.DIRECT

    return backend_type


def get_trust_mode() -> str:
    """Get the trust mode derived from the backend type.

    Returns:
        'trusted' for sandboxed_pool and direct backends.
        'untrusted' for ephemeral backend.
    """
    backend_type = _resolve_backend_type()
    return "untrusted" if backend_type.is_untrusted else "trusted"


class ExecutorSyncInput(BaseModel):
    repository_id: UUID4


class ResolvedContext(BaseModel):
    """Pre-resolved secrets and variables for untrusted execution mode.

    In untrusted mode, the sandbox doesn't have DB access, so secrets and
    variables are resolved by the caller and passed in this object.
    """

    secrets: dict[str, Any] = {}
    """Pre-resolved secrets keyed by secret name."""

    variables: dict[str, Any] = {}
    """Pre-resolved workspace variables keyed by variable name."""


class ExecutorActionErrorInfo(BaseModel):
    """An error that occurred in the registry."""

    action_name: str
    """Name of the action that failed."""

    type: str
    """Type of the error."""

    message: str
    """Error message."""

    filename: str
    """File where the error occurred."""

    function: str
    """Function where the error occurred."""

    lineno: int | None = None
    """Line number where the error occurred."""

    loop_iteration: int | None = None
    """Iteration number of the loop that caused the error."""

    loop_vars: dict[str, Any] | None = None
    """Variables of the loop that caused the error."""

    def __str__(self) -> str:
        parts = []
        msg = f"\n{self.type}: {self.message}"
        if self.loop_iteration is not None:
            parts.append(
                f"\n[for_each] (Iteration {self.loop_iteration})"
                f"\n\nLoop variables:\n```\n{json.dumps(self.loop_vars or {}, indent=2)}\n```"
                f"\n\n{msg}"
                "\n\nPlease ensure that the loop is iterable and that the loop variable has the correct type."
            )
        else:
            parts.append(msg)
        if TRACECAT__APP_ENV == "development":
            parts.append(
                f"\n\n{'-' * 30}"
                f"\nFile: {self.filename}"
                f"\nFunction: {self.function}"
                f"\nLine: {self.lineno}"
            )
        return "\n".join(parts)

    @staticmethod
    def from_exc(e: Exception, action_name: str) -> ExecutorActionErrorInfo:
        """Create an error info from an exception."""
        tb = traceback.extract_tb(e.__traceback__)[-1]  # Get the last frame
        return ExecutorActionErrorInfo(
            action_name=action_name,
            type=e.__class__.__name__,
            message=str(e),
            filename=tb.filename,
            function=tb.name,
            lineno=tb.lineno,
        )
