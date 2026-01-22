from __future__ import annotations

import json
import traceback
from datetime import datetime
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

    All sandbox backends use untrusted mode - DB credentials are never passed.

    - POOL: Warm nsjail workers (high throughput, single-tenant, untrusted)
    - EPHEMERAL: Cold nsjail subprocess per action (full isolation, multi-tenant, untrusted)
    - DIRECT: In-process execution (TESTING ONLY - no isolation, no subprocess overhead)
    - AUTO: Auto-select based on environment
    """

    POOL = "pool"
    EPHEMERAL = "ephemeral"
    DIRECT = "direct"
    AUTO = "auto"


def _is_nsjail_available() -> bool:
    """Check if nsjail is available in the current environment.

    Validates that nsjail_path is an executable file and rootfs_path is a directory.
    """
    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    return nsjail_path.is_file() and rootfs_path.is_dir()


def resolve_backend_type() -> ExecutorBackendType:
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
                "Auto-selecting 'pool' backend (nsjail available)",
            )
            backend_type = ExecutorBackendType.POOL
        else:
            logger.warning(
                "Auto-selecting 'direct' backend (nsjail not available)",
            )
            backend_type = ExecutorBackendType.DIRECT

    return backend_type


class ExecutorSyncInput(BaseModel):
    repository_id: UUID4


class ActionImplementation(BaseModel):
    """Action implementation metadata for sandbox execution.

    Contains everything needed to execute an action without DB access.
    """

    type: str
    """Action type: 'udf' or 'template'."""

    action_name: str | None = None
    """Registry action name (e.g., 'core.transform.reshape' or 'testing.my_template').

    This is preferred for loading actions in-process (e.g., DirectBackend) because it
    allows indexed lookups on (namespace, name) instead of slower JSON implementation
    scans.
    """

    module: str | None = None
    """Module path for UDF actions (e.g., 'tracecat_registry.integrations.core.transform')."""

    name: str | None = None
    """Function name for UDF actions (e.g., 'reshape')."""

    template_definition: dict[str, Any] | None = None
    """Template action definition for template actions."""

    origin: str | None = None
    """Origin URL for the action's registry (e.g., 'tracecat_registry' or 'git+ssh://...')."""


class ResolvedContext(BaseModel):
    """Pre-resolved context for untrusted execution mode.

    In untrusted mode, the sandbox doesn't have DB access, so all context
    needed to execute the action is resolved by the caller and passed here.
    """

    secrets: dict[str, Any] = {}
    """Pre-resolved secrets keyed by secret name."""

    variables: dict[str, Any] = {}
    """Pre-resolved workspace variables keyed by variable name."""

    action_impl: ActionImplementation
    """Action implementation metadata for direct execution without DB."""

    evaluated_args: dict[str, Any]
    """Pre-evaluated action arguments with all template expressions resolved."""

    # Execution context for SDK calls (used by warm workers with concurrent requests)
    workspace_id: str
    """Workspace UUID for SDK context."""

    workflow_id: str
    """Workflow UUID for SDK context."""

    run_id: str
    """Run UUID for SDK context."""

    executor_token: str
    """JWT token for SDK authentication."""

    logical_time: datetime | None = None
    """Logical time for deterministic FN.now() during workflow execution."""


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
