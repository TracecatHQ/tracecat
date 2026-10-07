"""Privacy-safe runtime classifications for durable agent failures."""

from __future__ import annotations

import signal
from dataclasses import dataclass

from tracecat.agent.common.exceptions import (
    AgentPreparationError,
    AgentSandboxProcessExitError,
    AgentToolLimitExceededError,
    AgentToolResolutionError,
    UserMCPDiscoveryAuthError,
    UserMCPDiscoveryBudgetExceededError,
    UserMCPDiscoveryTimeoutError,
    UserMCPDiscoveryUnavailableError,
)
from tracecat.agent.sandbox.config import AgentResourceLimits
from tracecat.exceptions import RegistryLockAmbiguousActionError
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.sanitization import redact_sensitive_text
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


# Caps for user-configured names echoed in messages, bounding their size.
_MAX_MESSAGE_NAME_LENGTH = 64
_MAX_MESSAGE_ACTION_NAMES = 5


def _message_name(name: str) -> str:
    if len(name) <= _MAX_MESSAGE_NAME_LENGTH:
        return name
    return f"{name[: _MAX_MESSAGE_NAME_LENGTH - 3]}..."


def _message_action_names(action_names: frozenset[str]) -> str:
    names = sorted(action_names)
    shown = ", ".join(_message_name(name) for name in names[:_MAX_MESSAGE_ACTION_NAMES])
    if (hidden := len(names) - _MAX_MESSAGE_ACTION_NAMES) > 0:
        return f"{shown} (+{hidden} more)"
    return shown


def invalid_agent_tools(
    error: AgentToolResolutionError,
) -> RuntimeErrorClassification:
    """Classify tools that reference missing or unbuildable custom actions."""
    problems: list[str] = []
    if error.missing_actions:
        problems.append(
            "Agent tools reference actions that are not in the registry: "
            f"{_message_action_names(error.missing_actions)}"
        )
    if error.failed_custom_actions:
        problems.append(
            "Custom registry actions could not be built into agent tools: "
            f"{_message_action_names(error.failed_custom_actions)}"
        )
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
        message="; ".join(problems),
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def agent_tool_limit_exceeded(
    error: AgentToolLimitExceededError, *, bedrock_workspace_chat: bool = False
) -> RuntimeErrorClassification:
    """Classify an agent that requests more tools than the configured limit."""
    message = f"Agent requests {error.requested} tools; the limit is {error.limit}"
    if bedrock_workspace_chat:
        # Bedrock has no tool search, so workspace chat keeps the cap.
        message += ". Bedrock does not support all tools; select specific tools"
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
        message=message,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def agent_tool_build_failure(
    error: ValueError, *, bedrock_workspace_chat: bool = False
) -> RuntimeErrorClassification:
    """Classify a failed agent tool build by owner.

    Missing platform actions and failed platform builds are platform-owned.
    Otherwise, entitlement-gated actions are a tenant plan gap, and missing or
    unbuildable custom registry actions are the caller's to fix.
    """
    if isinstance(error, AgentToolLimitExceededError):
        return agent_tool_limit_exceeded(
            error, bedrock_workspace_chat=bedrock_workspace_chat
        )
    if not isinstance(error, AgentToolResolutionError) or (
        error.missing_platform_actions or error.failed_actions
    ):
        return agent_preparation_failed(error, retryable=False)
    if error.entitlement_denied_actions:
        return tenant_entitlement_denied(error)
    if error.missing_actions or error.failed_custom_actions:
        return invalid_agent_tools(error)
    return agent_preparation_failed(error, retryable=False)


def agent_mcp_auth_failed(
    error: UserMCPDiscoveryAuthError,
) -> RuntimeErrorClassification:
    """Classify a user MCP server that rejected the configured credentials."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_MCP_AUTH_FAILED,
        message=(
            f"MCP server '{_message_name(error.server_name)}' rejected the "
            "configured credentials; reconnect the integration"
        ),
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def agent_mcp_unavailable(
    error: UserMCPDiscoveryUnavailableError,
) -> RuntimeErrorClassification:
    """Classify a user MCP server that was unreachable or rejected discovery."""
    server_name = _message_name(error.server_name)
    if isinstance(error, UserMCPDiscoveryBudgetExceededError):
        message = (
            f"MCP tool discovery ran out of time before reaching server "
            f"'{server_name}'; check availability of the other servers and retry"
        )
    elif isinstance(error, UserMCPDiscoveryTimeoutError):
        message = (
            f"MCP server '{server_name}' timed out during tool discovery; "
            "check availability and retry"
        )
    elif error.retryable:
        message = f"MCP server '{server_name}' is unavailable; retry later"
    else:
        message = f"MCP server '{server_name}' rejected the tool discovery request"
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_MCP_UNAVAILABLE,
        message=message,
        retry_disposition=(
            RetryDisposition.RETRYABLE
            if error.retryable
            else RetryDisposition.NON_RETRYABLE
        ),
        cause=error,
    )


def mcp_discovery_failure(error: BaseException) -> RuntimeErrorClassification:
    """Classify a user MCP discovery failure, defaulting to platform-owned."""
    if isinstance(error, UserMCPDiscoveryAuthError):
        return agent_mcp_auth_failed(error)
    if isinstance(error, UserMCPDiscoveryUnavailableError):
        return agent_mcp_unavailable(error)
    return agent_preparation_failed(error, retryable=False)


def registry_lock_invalid_data(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify deterministic registry lock resolution failures.

    Missing or unsupported actions are resolution gaps in the registry itself
    rather than a fault in the agent's configuration.
    """
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.REGISTRY_LOCK_INVALID_DATA,
        message="Tracecat could not resolve the agent's registry actions",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=error,
    )


def registry_lock_action_ambiguous(
    error: RegistryLockAmbiguousActionError,
) -> RuntimeErrorClassification:
    """Classify an action name that resolves to more than one registry.

    The collision comes from an org's custom registry shadowing another
    registry's action, so the org owns the fix and the message only exposes
    the action name and registry origins the org already controls.
    """
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.REGISTRY_LOCK_ACTION_AMBIGUOUS,
        message=str(error),
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


def agent_llm_provider_rejected_request(
    *,
    status_code: int,
    model: str | None,
    error_type: str | None,
    error_code: str | None,
    retryable: bool = False,
) -> RuntimeErrorClassification:
    """Classify a provider HTTP error with its safe machine-readable facts."""
    message = f"LLM provider rejected the request (HTTP {status_code}"
    detail = error_code or error_type
    if detail is not None:
        message += f", {detail}"
    message += ")"
    if model is not None:
        message += f" for model {model}"
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_EXECUTION_FAILED,
        message=message,
        retry_disposition=(
            RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
        ),
    )


def agent_llm_read_timeout(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Assign investigation of an ambiguous upstream read stall to Tracecat.

    Operational ownership is not root-cause attribution: a direct route alone
    cannot distinguish provider, network, or local proxy failures.
    """
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT,
        message="Timed out waiting for data from the LLM upstream",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=error,
    )


def agent_llm_gateway_auth_failed() -> RuntimeErrorClassification:
    """Classify a rejected internal gateway credential at its trusted source."""
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_LLM_GATEWAY_AUTH_FAILED,
        message="Tracecat could not authenticate to the LLM gateway",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )


def agent_llm_provider_auth_failed() -> RuntimeErrorClassification:
    """Classify rejected upstream provider credentials or permissions."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED,
        message="LLM provider authentication failed; check provider credentials and permissions",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )


def agent_llm_model_not_enabled() -> RuntimeErrorClassification:
    """Classify a managed-route model that workspace model access disallows."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
        message=(
            "The selected model is not enabled for this workspace; "
            "enable it in model access settings or choose another model"
        ),
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )


def agent_llm_budget_exceeded() -> RuntimeErrorClassification:
    """Classify an explicit budget denial that needs a limit or billing change."""
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
        message="LLM budget exhausted; check the configured budget or billing limits",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )


def agent_llm_rate_limited(*, route_is_direct: bool) -> RuntimeErrorClassification:
    """Classify temporary throttling without assuming that a budget ran out."""
    constructor = (
        RuntimeErrorClassification.user
        if route_is_direct
        else RuntimeErrorClassification.platform
    )
    return constructor(
        kind=RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
        message="LLM requests are temporarily rate limited; retry later",
        retry_disposition=RetryDisposition.RETRYABLE,
    )


AGENT_EXECUTOR_UNCLASSIFIED_MESSAGE = "Unclassified agent executor error"
MAX_UNCLASSIFIED_DETAIL_CHARS = 1000


def _unclassified_detail(
    error: BaseException | None,
    detail: str | None,
) -> str | None:
    if detail is None and error is not None:
        text = str(error).strip()
        detail = f"{type(error).__name__}: {text}" if text else type(error).__name__
    if not detail:
        return None
    safe = " ".join(redact_sensitive_text(detail, redact_emails=True).split())
    if len(safe) > MAX_UNCLASSIFIED_DETAIL_CHARS:
        safe = f"{safe[: MAX_UNCLASSIFIED_DETAIL_CHARS - 1]}…"
    return safe or None


def agent_executor_unclassified(
    error: BaseException | None = None,
    *,
    detail: str | None = None,
) -> RuntimeErrorClassification:
    """Classify an executor failure that no specific policy recognizes.

    The message keeps a redacted, bounded copy of the underlying error so the
    failure can be triaged without log access. ``detail`` overrides the text
    derived from ``error``.
    """
    message = AGENT_EXECUTOR_UNCLASSIFIED_MESSAGE
    if safe_detail := _unclassified_detail(error, detail):
        message = f"{message}: {safe_detail}"
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED,
        message=message,
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=error,
    )


def agent_sandbox_resource_limit_exceeded(
    error: BaseException | None = None,
) -> RuntimeErrorClassification:
    """Classify a jailed agent runtime that exceeded a resource limit.

    The cap is published deployment configuration the caller's workload
    exceeded, and a retry hits the same cap deterministically.
    """
    limits = AgentResourceLimits()
    return RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.SANDBOX_RESOURCE_LIMIT_EXCEEDED,
        message=(
            "The sandbox exceeded a resource limit (memory, CPU time, or file size). "
            f"The cgroup memory budget is {limits.memory_mb} MiB "
            "(TRACECAT__AGENT_SANDBOX_MEMORY_MB). "
            f"Per-process address space is capped at {limits.address_space_limit_mb} MiB "
            "(TRACECAT__AGENT_SANDBOX_ADDRESS_SPACE_MB; defaults to twice the memory budget)."
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
    the caller and carries its own message. A platform preparation error is a
    non-retryable preparation failure. Every other failure is a platform-owned
    unclassified executor error carrying the underlying error.
    """
    for cause in iter_error_chain(error):
        if isinstance(cause, AgentPreparationError):
            classification = agent_preparation_failed(cause, retryable=False)
            return AgentRuntimeFailure(
                message=classification.message,
                classification=classification,
            )
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
        classification=agent_executor_unclassified(error),
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
