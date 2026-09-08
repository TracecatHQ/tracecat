"""Privacy-safe runtime classifications for durable agent failures."""

from __future__ import annotations

import signal
from dataclasses import dataclass

from tracecat.agent.common.config import TRACECAT__AGENT_SANDBOX_MEMORY_MB
from tracecat.agent.common.exceptions import AgentSandboxProcessExitError
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.sandbox.exceptions import sandbox_resource_limit_message
from tracecat.temporal.errors import iter_error_chain

# Exit codes (``128 + signal``) that mean the jailed agent runtime hit one of
# its nsjail rlimits. SIGABRT is included here, unlike the core sandbox: the
# jailed process is the trusted shim plus the Claude Code CLI, whose JavaScript
# engine aborts on allocation failure and has no in-band channel comparable to
# Python's MemoryError. SIGKILL covers the OOM killer and the wall-clock limit,
# SIGXCPU the CPU-time limit, and SIGXFSZ the file-size limit.
AGENT_SANDBOX_RESOURCE_LIMIT_EXIT_CODES = frozenset(
    {
        128 + signal.SIGABRT,
        128 + signal.SIGKILL,
        128 + signal.SIGXCPU,
        128 + signal.SIGXFSZ,
    }
)


@dataclass(frozen=True, slots=True)
class AgentRuntimeFailure:
    """Terminal attribution plus the message safe to surface for a runtime failure."""

    message: str
    classification: RuntimeErrorClassification


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


def agent_sandbox_resource_limit_exceeded(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify a jailed agent runtime that died from one of its rlimits.

    The cap is published deployment configuration the caller's workload
    exceeded, and a retry hits the same cap deterministically.
    """
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.SANDBOX_RESOURCE_LIMIT_EXCEEDED,
        message=sandbox_resource_limit_message(
            memory_mb=TRACECAT__AGENT_SANDBOX_MEMORY_MB,
            memory_env_var="TRACECAT__AGENT_SANDBOX_MEMORY_MB",
        ),
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def agent_runtime_failure(
    error: BaseException,
    *,
    fallback_message: str,
) -> AgentRuntimeFailure:
    """Classify an exception raised out of a Claude runtime turn.

    A jailed process that exited with a resource-limit code is attributed to
    the caller and carries its own message. Every other failure remains
    platform-owned executor unavailability, surfacing ``fallback_message``.
    """
    for cause in iter_error_chain(error):
        if (
            isinstance(cause, AgentSandboxProcessExitError)
            and cause.exit_code in AGENT_SANDBOX_RESOURCE_LIMIT_EXIT_CODES
        ):
            classification = agent_sandbox_resource_limit_exceeded(cause)
            return AgentRuntimeFailure(
                message=classification.message,
                classification=classification,
            )
    return AgentRuntimeFailure(
        message=fallback_message,
        classification=agent_executor_unavailable(error),
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
