"""Red-teaming and security-oriented tests for Tracecat file tools.

These tests exercise edge cases such as directory traversal, absolute paths,
malformed regex patterns, and other security vulnerabilities to ensure our
validation logic and ModelRetry surfacing behave as expected.
"""

import os
import tempfile

import pytest
from pydantic_ai import ModelRetry
from tracecat_registry.integrations.agents.tools import create_secure_file_tools


class TestAgentSecurityValidation:
    """Comprehensive security validation tests for agent file tools and action filtering."""

    def _get_tool(self, tools, name: str):
        """Helper to extract a specific tool by name from the tools list."""
        return next(t for t in tools if t.name == name)

    def test_directory_traversal_blocked(self):
        """Attempting to create a file outside the temp dir should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = create_secure_file_tools(tmp)
            create_file = self._get_tool(tools, "create_file").function

            with pytest.raises(ValueError, match="Directory traversal is not allowed"):
                create_file("../hack.txt", "bad")

    def test_absolute_path_blocked(self):
        """Absolute paths are forbidden."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = create_secure_file_tools(tmp)
            read_file = self._get_tool(tools, "read_file").function

            with pytest.raises(ValueError, match="Absolute paths are not allowed"):
                read_file("/etc/passwd")

    def test_hidden_file_blocked(self):
        """Hidden files should be rejected by validation."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = create_secure_file_tools(tmp)
            create_file = self._get_tool(tools, "create_file").function

            with pytest.raises(ValueError, match="Hidden files are not allowed"):
                create_file(".secret", "nope")

    def test_path_length_limit_enforced(self):
        """File paths longer than the allowed limit (1000 chars) should be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = create_secure_file_tools(tmp)
            create_file = self._get_tool(tools, "create_file").function

            long_path = "a" * 1001  # 1001 > 1000 char limit
            with pytest.raises(ValueError, match="File path too long"):
                create_file(long_path, "data")

    def test_windows_device_names_blocked(self):
        """Windows reserved device names must be rejected even on non-Windows systems."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = create_secure_file_tools(tmp)
            create_file = self._get_tool(tools, "create_file").function

            with pytest.raises(ValueError, match="Windows device name not allowed"):
                create_file("NUL", "oops")

    def test_binary_file_read_protection(self):
        """Attempting to read a binary file should raise a ValueError indicating the file is not text."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a fake binary file inside the temp dir
            binary_path = os.path.join(tmp, "binary.dat")
            with open(binary_path, "wb") as fh:
                fh.write(b"\x00\x01\x02\x03")

            tools = create_secure_file_tools(tmp)
            read_file_tool = self._get_tool(tools, "read_file").function

            # Provide the relative path
            rel_path = os.path.relpath(binary_path, tmp)
            with pytest.raises(ValueError, match="binary"):
                read_file_tool(rel_path)

    def test_malformed_regex_raises_modelretry(self):
        """grep_search should raise ModelRetry for malformed regex patterns."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = create_secure_file_tools(tmp)
            grep_tool = self._get_tool(tools, "grep_search").function

            # Unclosed group
            bad_pattern = "(unclosed"
            with pytest.raises(ModelRetry):
                grep_tool(bad_pattern)

    @pytest.mark.anyio
    async def test_forbidden_action_whitelist_enforcement(self):
        """Building an agent with an action outside the allowed list should fail fast."""
        from tracecat_registry.integrations.agents.builder import agent

        with pytest.raises(ValueError, match="Forbidden actions"):
            await agent(
                user_prompt="Should never run",
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=["evil.action"],
            )
