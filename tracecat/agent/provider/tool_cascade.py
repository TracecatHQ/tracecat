"""Pure resolution helper for the agent tools allowlist cascade.

Kept dependency-free (no Temporal, no DB, no Pydantic) so it can be
unit tested in isolation and replayed inside Temporal workflows
without sandbox passthrough concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolsSource = Literal["default", "source", "action"]


@dataclass(frozen=True, slots=True)
class ResolvedAllowedTools:
    """Cascaded ``allowed_tools`` value ready to hand to the runtime.

    ``allowed_tools`` keeps the tristate semantics of the underlying
    SDK option:

    - ``None``: no override at any level — the runtime keeps the SDK
      default (full built-in toolset).
    - ``[]``: explicit empty list — the runtime disables every
      built-in tool. Distinct from ``None``.
    - non-empty list: whitelist of tool names the runtime is allowed
      to enable.

    ``source`` carries diagnostic metadata indicating which level of
    the cascade actually contributed the value, useful for logs and
    troubleshooting output.
    """

    allowed_tools: list[str] | None
    source: ToolsSource


def resolve_allowed_tools(
    *,
    source_value: list[str] | None,
    action_value: list[str] | None,
) -> ResolvedAllowedTools:
    """Apply the action > source > default cascade.

    Action-level value wins over source-level value. ``None`` at a
    given level means "no opinion at this layer, defer to the next".
    An empty list at either level is a value (it carries the explicit
    "disable all" intent), so it propagates through the cascade
    untouched.
    """
    if action_value is not None:
        return ResolvedAllowedTools(allowed_tools=action_value, source="action")
    if source_value is not None:
        return ResolvedAllowedTools(allowed_tools=source_value, source="source")
    return ResolvedAllowedTools(allowed_tools=None, source="default")
