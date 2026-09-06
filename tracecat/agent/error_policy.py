"""Privacy-safe runtime classifications for durable agent failures."""

from __future__ import annotations

from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)


def invalid_agent_configuration(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify deterministic caller-owned agent configuration failures."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
        message="Agent configuration is invalid",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def tenant_entitlement_denied(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify a deterministic tenant feature-entitlement denial."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
        message="This feature requires an upgraded plan",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def agent_preparation_failed(
    error: BaseException | None = None,
    *,
    retryable: bool,
) -> RuntimeErrorClassification:
    """Classify trusted workflow failures while preparing an agent run."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_PREPARATION_FAILED,
        message="Tracecat could not prepare the agent run",
        retry_disposition=(
            RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
        ),
        cause=error,
    )


def agent_session_initialization_failed(
    error: BaseException | None = None,
    *,
    retryable: bool,
) -> RuntimeErrorClassification:
    """Classify failures establishing trusted durable session state."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_SESSION_INITIALIZATION_FAILED,
        message="Tracecat could not initialize the agent session",
        retry_disposition=(
            RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
        ),
        cause=error,
    )


def user_agent_execution_failed(
    error: BaseException | None = None,
    *,
    retryable: bool = False,
) -> RuntimeErrorClassification:
    """Classify an error owned by the agent caller or its direct provider."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_EXECUTION_FAILED,
        message="Agent execution failed",
        retry_disposition=(
            RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
        ),
        cause=error,
    )


def agent_executor_unavailable(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify trusted executor infrastructure or transport failures."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
        message="Tracecat agent executor is unavailable",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=error,
    )


def agent_executor_timed_out(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify executor activity or runtime deadline exhaustion."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT,
        message="Tracecat agent execution timed out",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=error,
    )


def agent_executor_protocol_failed(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify invalid or incomplete executor protocol outcomes."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED,
        message="Tracecat agent executor returned an invalid result",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def agent_workflow_internal_error(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify the workflow's final invariant fallback."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_WORKFLOW_INTERNAL_ERROR,
        message="Tracecat agent workflow encountered an internal error",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )
