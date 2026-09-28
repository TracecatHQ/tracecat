"""Tests for the portable SKILL.md frontmatter contract."""

import pytest
from pydantic import ValidationError

from tracecat.agent.skill.frontmatter import parse_skill_markdown


def test_parse_skill_frontmatter_canonicalizes_and_deduplicates_tools() -> None:
    parsed = parse_skill_markdown(
        """---
name: incident-triage
license: Apache-2.0
metadata:
  owner: security
  tools:
    - " core.cases.get_case "
    - mcp.slack.post_message
    - core.cases.get_case
---

# Incident triage
"""
    )

    assert parsed is not None
    assert parsed.metadata.tools == [
        "core.cases.get_case",
        "mcp.slack.post_message",
    ]
    assert parsed.model_dump()["license"] == "Apache-2.0"
    assert parsed.metadata.model_dump()["owner"] == "security"


def test_parse_skill_frontmatter_accepts_underscore_mcp_slug() -> None:
    parsed = parse_skill_markdown(
        "---\nname: example\nmetadata:\n  tools:\n    - mcp.github_mcp\n---\n"
    )

    assert parsed is not None
    assert parsed.metadata.tools == ["mcp.github_mcp"]


@pytest.mark.parametrize(
    "tool_id",
    [
        "mcp",
        "mcp.slack.issue.get",
        "mcp.Slack.post_message",
        "MCP.slack.post_message",
        "core",
        "core.Cases.get_case",
        "core..get_case",
    ],
)
def test_parse_skill_frontmatter_rejects_invalid_tool_ids(tool_id: str) -> None:
    with pytest.raises(ValidationError):
        parse_skill_markdown(
            f"""---
name: incident-triage
metadata:
  tools:
    - {tool_id}
---
"""
        )


def test_parse_skill_frontmatter_caps_tool_count() -> None:
    tools = "\n".join(f"    - ns.tool_{index}" for index in range(65))

    with pytest.raises(ValidationError):
        parse_skill_markdown(
            f"""---
name: incident-triage
metadata:
  tools:
{tools}
---
"""
        )
