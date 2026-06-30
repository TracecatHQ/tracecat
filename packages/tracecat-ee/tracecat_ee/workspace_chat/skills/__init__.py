"""Built-in workspace-chat copilot skills (Enterprise Edition).

These skills are plain on-disk skill directories (``<name>/SKILL.md`` plus
optional ``references/*.md``) that the agent executor stages into the copilot's
``~/.claude/skills`` directory for every entitled workspace-chat session,
independent of any agent preset. The claude_code runtime then discovers and
fetches them on demand, so the guidance reaches the model regardless of model
strength.

Every built-in skill name MUST use the reserved ``tracecat-`` prefix. User and
preset skill names are forbidden from using that prefix (enforced in
``tracecat.agent.skill.schemas``), so a built-in skill can never collide with a
user-authored one in the staged skills directory.
"""

from __future__ import annotations

# Reserved name prefix that only platform/built-in skills may use.
BUILTIN_SKILL_NAME_PREFIX = "tracecat-"

# Canonical, always-on skills staged for every entitled workspace-chat session.
# Each entry MUST be the name of a directory under this package that contains a
# ``SKILL.md`` file, and MUST start with ``BUILTIN_SKILL_NAME_PREFIX``.
BUILTIN_WORKSPACE_CHAT_SKILLS: tuple[str, ...] = (
    "tracecat-manage-workflows",
    "tracecat-platform-guide",
)

__all__ = [
    "BUILTIN_SKILL_NAME_PREFIX",
    "BUILTIN_WORKSPACE_CHAT_SKILLS",
]
