"""Shared schema-level validators for agent configuration."""

from __future__ import annotations

from tracecat import config


def validate_actions_length(value: list[str] | None) -> list[str] | None:
    """Ensure the ``actions`` list does not exceed the configured tool cap.

    Raised as a ``ValueError`` so it surfaces as a Pydantic ``ValidationError``
    at schema validation time, instead of failing later inside the agent
    worker when tools are materialized.
    """
    if value is None:
        return value
    max_tools = config.TRACECAT__AGENT_MAX_TOOLS
    if max_tools > 0 and len(value) > max_tools:
        raise ValueError(
            f"An agent can reference at most {max_tools} actions, got {len(value)}. "
            "Reduce the number of tools attached to the agent or raise "
            "TRACECAT__AGENT_MAX_TOOLS."
        )
    return value
