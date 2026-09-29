from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import UUID4, BaseModel, Field

from tracecat import config
from tracecat.config import TRACECAT__APP_ENV
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.executor.types import ExecutorBackendType
from tracecat.secrets.common import CapturedFailure, MaskedSecretError


def _failure_site(e: Exception) -> CapturedFailure:
    """Original failure type and location.

    A masked exception's own traceback points at the masking boundary; prefer
    the site captured before the original traceback was dropped.
    """
    if isinstance(e, MaskedSecretError) and e.captured is not None:
        return e.captured
    return CapturedFailure.from_exc(e)


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


def resolve_backend_type() -> ExecutorBackendType:
    """Resolve the configured backend, including the legacy ephemeral alias."""
    return ExecutorBackendType.from_config(config.TRACECAT__EXECUTOR_BACKEND)


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

    This is preferred for loading actions in-process (e.g., TestBackend) because it
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
    """Pre-resolved secrets keyed by secret name.

    Used for expression evaluation (e.g. ``${{ SECRETS.aws.AWS_ROLE_ARN }}``).
    Never mutated by host-side credential resolution.
    """

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

    secret_projection: SecretEnvProjection | None = Field(default=None, exclude=True)
    """Runtime-ready secret env cached for host-side execution reuse."""


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
    """Deprecated. Never populated: loop values are withheld like `var.*`.

    Retained because the generated API client still declares the field.
    """

    def __str__(self) -> str:
        parts = []
        msg = f"\n{self.type}: {self.message}"
        if self.loop_iteration is not None:
            parts.append(
                f"\n[for_each] (Iteration {self.loop_iteration})"
                f"\n{msg}"
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
        site = _failure_site(e)
        return ExecutorActionErrorInfo(
            action_name=action_name,
            type=site.original_type,
            message=str(e),
            filename=site.filename,
            function=site.function,
            lineno=site.lineno,
        )
