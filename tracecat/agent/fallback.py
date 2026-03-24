from __future__ import annotations

from dataclasses import dataclass

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


def format_model_target(target: AgentModelConfig) -> str:
    """Return a compact human-readable identifier for a model candidate."""

    return f"{target.model_provider}/{target.model_name}"


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
