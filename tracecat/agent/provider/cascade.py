"""Pure resolution helpers for the agent system-prompt cascade.

Kept dependency-free (no Temporal, no DB, no Pydantic) so it can be unit
tested in isolation and replayed inside Temporal workflows without
sandbox passthrough concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PromptSource = Literal["default", "source", "action"]


@dataclass(frozen=True, slots=True)
class ResolvedSystemPromptOverrides:
    """Cascaded system-prompt overrides ready to hand to the runtime.

    All fields are nullable. ``None`` for ``replace`` means *keep the
    default Tracecat baseline*; ``None`` for ``append`` means *no extra
    text*. ``replace_source`` and ``append_count`` are diagnostic
    metadata exposed to logs and (eventually) to ``runtime_resolution``
    on the action output.
    """

    replace: str | None
    append: str | None
    replace_source: PromptSource
    append_count: int


def resolve_system_prompt_overrides(
    *,
    source_replace: str | None,
    source_append: str | None,
    action_replace: str | None,
    action_append: str | None,
) -> ResolvedSystemPromptOverrides:
    """Apply the action > source > default cascade.

    Rules:
    - ``replace``: action wins. If neither action nor source set it,
      ``replace`` is ``None`` and the runtime keeps its default
      baseline.
    - ``append``: contributions from source and action *cumulate* in
      that order, separated by a blank line. Either being ``None`` or
      empty is silently skipped.

    The legacy per-action ``Instructions`` field is **not** part of this
    cascade — it is consumed directly by the runtime as a third append
    contributor for backward compatibility.
    """
    if action_replace is not None:
        replace = action_replace
        replace_source: PromptSource = "action"
    elif source_replace is not None:
        replace = source_replace
        replace_source = "source"
    else:
        replace = None
        replace_source = "default"

    parts: list[str] = []
    if source_append:
        parts.append(source_append)
    if action_append:
        parts.append(action_append)
    append = "\n\n".join(parts) if parts else None

    return ResolvedSystemPromptOverrides(
        replace=replace,
        append=append,
        replace_source=replace_source,
        append_count=len(parts),
    )
