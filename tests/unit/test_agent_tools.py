"""Unit tests for agent tools module.

Tests the harness-agnostic tool creation and execution:
- denormalize_tool_name utility
- build_agent_tools filtering
"""

import pytest

from tracecat.agent.tools import build_agent_tools, denormalize_tool_name

# =============================================================================
# build_agent_tools Tests
# =============================================================================


class TestBuildAgentTools:
    """Tests for shared agent tool filtering."""

    @pytest.mark.anyio
    async def test_excludes_agent_actions_before_registry_lookup(self) -> None:
        """Excluded actions are unavailable to every agent harness."""
        result = await build_agent_tools(actions=["core.script.run_python"])

        assert result.tools == []
        assert result.collected_secrets == set()


# =============================================================================
# denormalize_tool_name Tests
# =============================================================================


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("core__http_request", "core.http_request"),
        ("tools__slack__post_message", "tools.slack.post_message"),
        ("core.http_request", "core.http_request"),
        (
            "tools__integrations__aws__s3__list_buckets",
            "tools.integrations.aws.s3.list_buckets",
        ),
    ],
)
def test_denormalize_tool_name(name: str, expected: str) -> None:
    """Double underscores become dots; single underscores are preserved."""
    assert denormalize_tool_name(name) == expected
