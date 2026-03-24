from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tracecat.agent.types import (
    AgentConfig,
    AgentModelConfig,
    get_model_candidates,
    replace_model_target,
)


@dataclass(frozen=True, slots=True)
class FallbackAttemptFailure:
    """Captured failure for a single model candidate."""

    target: AgentModelConfig
    error: str


class FallbackFailureScope(StrEnum):
    """Classify whether a failure belongs to the provider or the platform."""

    PROVIDER_LOCAL = "provider_local"
    PLATFORM_LOCAL = "platform_local"
    UNKNOWN = "unknown"


_PROVIDER_LOCAL_ERROR_MARKERS = (
    "authentication failed - check your api credentials",
    "access denied - check your api permissions",
    "model not found - check your model configuration",
    "rate limit exceeded - please try again later",
    "llm provider internal error",
    "llm provider unavailable",
    "llm provider temporarily unavailable",
    "llm provider request timed out",
    "invalid x-api-key",
    "authentication_error",
    "rate limit",
    "overloaded",
)

_PLATFORM_LOCAL_ERROR_MARKERS = (
    "runtime disconnected during execution",
    "runtime exited cleanly but loopback result was not received",
    "runtime process exited with code",
    "agent execution timed out after",
    "proxy error:",
    "malformed request line",
    "invalid request encoding",
    "request body too large",
)


def format_model_target(target: AgentModelConfig) -> str:
    """Return a compact human-readable identifier for a model candidate."""

    return f"{target.model_provider}/{target.model_name}"


def classify_fallback_failure(error: str | None) -> FallbackFailureScope:
    """Return the failure scope for deciding whether the same turn can be retried."""

    if error is None:
        return FallbackFailureScope.UNKNOWN

    normalized = error.lower()
    if any(marker in normalized for marker in _PROVIDER_LOCAL_ERROR_MARKERS):
        return FallbackFailureScope.PROVIDER_LOCAL
    if any(marker in normalized for marker in _PLATFORM_LOCAL_ERROR_MARKERS):
        return FallbackFailureScope.PLATFORM_LOCAL
    return FallbackFailureScope.UNKNOWN


def should_retry_same_turn(error: str | None) -> bool:
    """Return True when a fallback should replay the same turn on the next candidate."""

    return classify_fallback_failure(error) is FallbackFailureScope.PROVIDER_LOCAL


def format_fallback_failure_message(
    failures: list[FallbackAttemptFailure],
) -> str:
    """Build a single error message describing all attempted candidates."""

    parts = [
        f"{format_model_target(failure.target)}: {failure.error}"
        for failure in failures
    ]
    return "All configured models failed. " + " | ".join(parts)


def get_fallback_configs(
    config: AgentConfig,
) -> list[tuple[AgentModelConfig, AgentConfig]]:
    """Return candidate configs in execution order."""

    return [
        (target, replace_model_target(config, target))
        for target in get_model_candidates(config)
    ]
