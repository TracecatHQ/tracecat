"""Tracecat execution guidance applied only to activated library skills."""

from tracecat.agent.skill.frontmatter import (
    normalize_skill_markdown,
    split_skill_markdown_frontmatter,
)

LIBRARY_SKILL_EXECUTION_NOTES = """## Tracecat execution notes

Follow the upstream workflow below using tools authorized for this agent. These
notes take precedence over upstream setup instructions and command examples.

- Before attempting upstream CLI commands or helper scripts, inspect available
  Tracecat action and MCP tool schemas and use an equivalent authorized tool.
- Preserve the intended operation, filters, pagination, confirmation, and
  verification requirements. Never bypass tool permissions or approvals.
- Do not install upstream plugins, CLIs, or their dependencies in the agent
  environment, or assume their original paths and credentials exist.
- For Python computation, use `core.script.run_python` when available and pass
  required Python packages through its `dependencies` parameter. Those packages
  are installed for that action, not in the agent environment.
- If available tools cannot perform a required operation, report the missing
  capability. Do not attempt upstream setup commands or fabricate results.

## Upstream skill instructions
"""


def render_library_skill_markdown(markdown: bytes) -> bytes:
    """Insert execution notes after frontmatter without editing catalog content."""
    parts = split_skill_markdown_frontmatter(
        normalize_skill_markdown(markdown.decode("utf-8"))
    )
    if parts is None:
        raise ValueError("Library skill is missing frontmatter")
    frontmatter, body = parts
    return (
        f"---\n{frontmatter}\n---\n{LIBRARY_SKILL_EXECUTION_NOTES}\n{body}"
    ).encode()
