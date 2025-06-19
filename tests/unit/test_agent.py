"""Integration and live tests for Tracecat agent functionality.

These tests were moved from tests/unit/test_agent_builder.py to separate
integration-oriented tests from unit tests for easier maintenance.
"""

import base64
import inspect
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest
from dotenv import load_dotenv
from pydantic_ai.tools import Tool
from tracecat_registry.integrations.agents.builder import (
    TracecatAgentBuilder,
    agent,
)

from tests.conftest import (
    requires_slack_mocks,
    skip_if_no_slack_credentials,
    skip_if_no_slack_token,
)

# Load environment variables from .env file for live tests
load_dotenv()


@pytest.mark.anyio
class TestAgentBuilderIntegration:
    """Integration test suite for TracecatAgentBuilder with real registry actions."""

    async def test_agent_with_core_actions_integration(self, test_role):
        """Test building and using an agent with real core actions."""
        # Build an agent with core actions
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a helpful assistant that can make HTTP requests and transform data.",
        )

        # Filter to only core actions for this test
        agent_instance = await builder.with_namespace_filters("core").build()

        # Verify the agent was created
        assert agent_instance is not None

        # Verify tools were loaded
        assert len(builder.tools) > 0

        # Check that we have some expected core tools
        tool_names = [tool.function.__name__ for tool in builder.tools]

        # Should have core HTTP and transform tools
        expected_tools = ["core__http_request", "core__reshape"]
        found_tools = [name for name in expected_tools if name in tool_names]
        assert len(found_tools) > 0, (
            f"Expected to find some of {expected_tools} in {tool_names}"
        )

    async def test_agent_with_python_script_action(self, test_role):
        """Test building an agent with the core Python script action."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a helpful assistant that can run Python scripts.",
        )

        # Filter to only the Python script action
        agent_instance = await builder.with_action_filters(
            "core.script.run_python"
        ).build()

        # Verify the agent was created
        assert agent_instance is not None

        # Should have exactly one tool
        assert len(builder.tools) == 1

        # Verify it's the Python script tool
        tool = builder.tools[0]
        assert tool.function.__name__ == "core__script__run_python"

        # Verify the tool has the expected parameters
        sig = inspect.signature(tool.function)
        params = list(sig.parameters.keys())

        # Should have the main parameters from the Python script action
        expected_params = [
            "script",
            "inputs",
            "dependencies",
            "timeout_seconds",
            "allow_network",
        ]
        for param in expected_params:
            assert param in params, (
                f"Expected parameter '{param}' not found in {params}"
            )

    @skip_if_no_slack_token
    @requires_slack_mocks
    async def test_agent_with_template_action_integration(self, mock_slack_secrets):
        """Test building an agent with a template action."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a helpful assistant that can post Slack messages.",
        )

        # Filter to only Slack template actions
        agent_instance = await builder.with_namespace_filters("tools.slack").build()

        # Verify the agent was created
        assert agent_instance is not None

        # Should have some tools
        assert len(builder.tools) > 0

        # Check for Slack-related tools
        tool_names = [tool.function.__name__ for tool in builder.tools]
        slack_tools = [name for name in tool_names if "slack" in name.lower()]
        assert len(slack_tools) > 0, f"Expected to find Slack tools in {tool_names}"

        # Verify at least one tool has the expected Slack parameters
        slack_tool = None
        for tool in builder.tools:
            if "post_message" in tool.function.__name__:
                slack_tool = tool
                break

        if slack_tool:
            sig = inspect.signature(slack_tool.function)
            params = list(sig.parameters.keys())

            # Should have channel parameter for Slack message posting
            assert "channel" in params, (
                f"Expected 'channel' parameter in Slack tool, got {params}"
            )

    @pytest.mark.anyio
    @skip_if_no_slack_credentials
    @requires_slack_mocks
    @pytest.mark.parametrize(
        "prompt_type,prompt_template",
        [
            (
                "simple",
                "Post a message to Slack asking: 'Which programming language do you prefer? "
                "Python 🐍 or JavaScript ⚡? Let us know in the comments!' "
                "Post this to channel: {channel}",
            ),
            # (
            #     "medium",
            #     "Post an interactive message to Slack asking people to vote between Python and JavaScript. "
            #     "Include two buttons: 'Python 🐍' and 'JavaScript ⚡'. "
            #     "Use simple Slack blocks format. "
            #     "Post this to channel: {channel}",
            # ),
            # (
            #     "complex",
            #     "Post a fun interactive message to the Slack channel asking people to vote on "
            #     "which is the better programming language. The message should include:\n"
            #     "1. A header section with an emoji and title 'Which is the better programming language?'\n"
            #     "2. Two comparison sections side by side:\n"
            #     "   - Python: 'Simple, readable, has pandas' \n"
            #     "   - JavaScript: 'Runs everywhere, async/await, has npm chaos'\n"
            #     "3. Two action buttons: 'Python 🐍' (green/primary) and 'JavaScript ⚡' (yellow/secondary)\n"
            #     "4. A context section with a fun note\n"
            #     "5. Use proper Slack block kit JSON format with sections, actions, and context blocks\n"
            #     "Post this to channel: {channel}",
            # ),
        ],
        ids=[
            "simple",
            "medium",
            "complex",
        ],
    )
    async def test_agent_live_slack_prompts(
        self, test_role, prompt_type, prompt_template, mock_slack_secrets, slack_secret
    ):
        """Live test: Agent creates Slack messages with varying complexity levels."""

        # Get environment variables
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        slack_channel = os.getenv("SLACK_CHANNEL_ID")

        if not slack_token or not slack_channel:
            pytest.skip("Slack credentials not available")

        # Set higher retries for complex prompts to handle flakiness
        retries = 5 if prompt_type == "complex" else 3

        # Build an agent with Slack capabilities
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions=(
                "You are a helpful assistant that can post interactive Slack messages. "
                "When asked to create interactive messages, use proper Slack block kit format "
                "with buttons, sections, and other interactive elements. "
                "If complex blocks fail, try simpler alternatives."
            ),
            retries=retries,
        )

        # Filter to Slack tools
        agent_instance = await builder.with_namespace_filters("tools.slack").build()

        # Verify agent was created with Slack tools
        assert agent_instance is not None
        assert len(builder.tools) > 0

        # Format the prompt with the channel
        prompt = prompt_template.format(channel=slack_channel)

        print(f"\n🤖 Running agent with {prompt_type} Slack prompt...")
        print(f"📝 Prompt: {prompt}")

        # Run the agent - don't catch exceptions, let them fail the test immediately
        result = await agent_instance.run(prompt)
        print(f"📤 Result: {result}")
        assert isinstance(result.output, str)

        # Should mention successful posting or contain message details
        result_lower = result.output.lower()
        success_indicators = [
            "posted",
            "sent",
            "message",
            "slack",
            "channel",
            "success",
            "python",
            "javascript",
            "programming",
        ]

        found_indicators = [
            indicator for indicator in success_indicators if indicator in result_lower
        ]

        assert len(found_indicators) > 0, (
            f"Expected success indicators in result: {result.output}"
        )

    @skip_if_no_slack_credentials
    @requires_slack_mocks
    async def test_agent_function_direct(
        self, mock_slack_secrets, slack_secret, test_role
    ):
        """Live test: Test the agent registry function directly."""

        # Get environment variables
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        slack_channel = os.getenv("SLACK_CHANNEL_ID")

        if not slack_token or not slack_channel:
            pytest.skip("Slack credentials not available")

        # Call the agent function directly
        result = await agent(
            user_prompt=(
                f"Post a simple message to Slack channel {slack_channel} saying "
                "'Hello from the Tracecat AI agent! 🤖 This is a test message.'"
            ),
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=["tools.slack.post_message"],
            instructions="You are a helpful assistant that can post messages to Slack.",
            include_usage=True,
        )

        print(f"\n🤖 Agent function result: {result}")

        # Verify the result structure
        assert isinstance(result, dict)
        assert "output" in result
        assert "message_history" in result
        assert "duration" in result
        assert "usage" in result

        # Verify the output contains success indicators
        output = result["output"]
        output_lower = str(output).lower()
        success_indicators = ["posted", "message", "slack", "hello", "test"]
        found_indicators = [
            indicator for indicator in success_indicators if indicator in output_lower
        ]
        assert len(found_indicators) > 0, (
            f"Expected success indicators in output: {output}"
        )

        # Verify message history exists
        assert isinstance(result["message_history"], list)
        assert len(result["message_history"]) > 0

        # Verify usage information
        assert isinstance(result["usage"], dict)

        print(
            f"🎉 Agent function test successful! Found indicators: {found_indicators}"
        )
        print(f"📊 Usage: {result['usage']}")
        print(f"⏱️ Duration: {result['duration']:.2f}s")

    @pytest.mark.anyio
    async def test_agent_with_mock_action_and_secrets(self, test_role, mocker):
        """Integration test: Agent using an action that requires secrets."""
        # This test verifies that when an agent uses tools, the secrets are properly fetched

        # Mock a simple action that uses secrets
        async def mock_action_func(message: str) -> str:
            # In a real action, this would use secrets from the environment
            return f"Processed: {message}"

        # Create a mock tool
        mock_tool = Tool(mock_action_func)

        # Mock the builder to return our mock tool
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a test assistant.",
        )

        # Mock create_tool_from_registry to return our mock tool
        async def mock_create_tool(*args, **kwargs) -> Tool:
            return mock_tool

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.create_tool_from_registry",
            side_effect=mock_create_tool,
        )

        # Mock the registry service to return a test action
        mock_reg_action = Mock()
        mock_reg_action.namespace = "test"
        mock_reg_action.name = "mock_action"

        mock_service = Mock()
        mock_service.list_actions = AsyncMock(return_value=[mock_reg_action])
        mock_service.fetch_all_action_secrets = AsyncMock(return_value=[])

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        )

        # Mock env_sandbox
        mocker.patch("tracecat_registry.integrations.agents.builder.env_sandbox")

        # Build the agent
        await builder.with_namespace_filters("test").build()

        # Verify the agent was built with the tool
        assert len(builder.tools) == 1
        assert builder.tools[0] == mock_tool

    @pytest.mark.anyio
    @skip_if_no_slack_credentials
    @requires_slack_mocks
    async def test_agent_post_file_contents_to_slack(
        self,
        test_role,
        mock_slack_secrets,
        slack_secret,
    ):
        """Test that the agent can receive files, read them via default tools, and post their contents to Slack."""
        import base64

        # Prepare sample file to inject into the agent's temp directory
        file_name = "greeting.txt"
        file_content = "Hello from Tracecat file 🗃️"
        files = {file_name: base64.b64encode(file_content.encode()).decode()}

        # Environment variables needed for Slack
        slack_channel = os.getenv("SLACK_CHANNEL_ID")
        if not slack_channel:
            pytest.skip("SLACK_CHANNEL_ID not available in environment")

        # Prompt instructing the agent to read the file and post it to Slack
        prompt = (
            f"Read the contents of '{file_name}' and post them to Slack channel {slack_channel}. "
            "Respond with a confirmation once done."
        )

        result = await agent(
            user_prompt=prompt,
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=["tools.slack.post_message"],
            files=files,
            instructions=(
                "You are a helpful assistant that can read files using the available file tools "
                "and then post their contents to Slack."
            ),
            include_usage=True,
        )

        # Basic structure assertions
        assert isinstance(result, dict)
        assert "output" in result
        assert "files" in result  # Returned files from temp directory

        # Ensure our file persisted through the agent run
        returned_files = result.get("files") or {}
        assert isinstance(returned_files, dict)
        assert file_name in returned_files
        assert returned_files[file_name] == file_content

        # Check that the agent output indicates success and mentions the file content
        output = str(result["output"]).lower()
        success_indicators = [
            "posted",
            "slack",
            "message",
            "hello",
            "tracecat",
        ]
        assert any(ind in output for ind in success_indicators)

    @pytest.mark.anyio
    @pytest.mark.skip(reason="Skipping until agents support empty actions")
    async def test_agent_jsonpath_find_and_replace(self, test_role):
        """Ensure agent can use default file tools to modify nested JSON via JSONPath and return updated file."""

        # Deeply nested JSON
        json_data = {
            "user": {
                "profile": {"details": {"address": {"city": "OldCity", "zip": "12345"}}}
            }
        }
        file_name = "data.json"
        file_content_str = json.dumps(json_data, indent=2)
        files = {file_name: base64.b64encode(file_content_str.encode()).decode()}

        prompt = (
            "Locate the field containing the value 'OldCity' inside the JSON file "
            f"`{file_name}`, then use the file manipulation tools to change it to 'NewCity'. "
            "You may first search the file with `grep_search` or `jsonpath_find` to discover the correct JSONPath, "
            "then apply `jsonpath_find_and_replace` to perform the update. Confirm when done."
        )

        # Patch RegistryActionsService to return no registry actions to avoid EE module import errors
        mock_service = Mock()
        mock_service.list_actions = AsyncMock(return_value=[])
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        with patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        ):
            result = await agent(
                user_prompt=prompt,
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=[],  # we rely on default file tools only
                files=files,
                instructions=(
                    "You are a helpful assistant that can manipulate JSON files using the available file tools."
                ),
                include_usage=True,
            )

        assert isinstance(result, dict)
        returned_files = result.get("files") or {}
        assert isinstance(returned_files, dict)
        assert file_name in returned_files

        updated_json = json.loads(returned_files[file_name])
        assert (
            updated_json["user"]["profile"]["details"]["address"]["city"] == "NewCity"
        )

        # Output confirmation should mention success
        assert "newcity" in str(result["output"]).lower()

        # Ensure the agent actually invoked jsonpath_find_and_replace
        history = result.get("message_history", [])
        tool_calls = [
            part["tool_name"]
            for msg in history
            if isinstance(msg, dict)
            for part in msg.get("parts", [])
            if isinstance(part, dict) and part.get("part_kind") == "tool-call"
        ]
        assert any("jsonpath_find_and_replace" in t for t in tool_calls)

    @pytest.mark.anyio
    @pytest.mark.skip(reason="Skipping until agents support empty actions")
    async def test_agent_multiple_file_operations(self, test_role):
        """Agent should replace text in multiple files and create a new summary file."""
        # Prepare two text files
        file_a = "greetings.txt"
        file_b = "fruits.txt"
        content_a = "Hello OldValue\nLine2"
        content_b = "OldValue\napples\nOldValue"
        files = {
            file_a: base64.b64encode(content_a.encode()).decode(),
            file_b: base64.b64encode(content_b.encode()).decode(),
        }

        prompt = (
            "Across all files replace the string 'OldValue' with 'NewValue'. "
            "After replacing, create a new file named 'summary.txt' that contains the single line 'Done'. "
            "Respond with confirmation."
        )

        # Patch registry service to avoid external deps
        mock_service = Mock()
        mock_service.list_actions = AsyncMock(return_value=[])
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        with patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        ):
            result = await agent(
                user_prompt=prompt,
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=[],
                files=files,
                instructions=(
                    "You are a helpful assistant that can manipulate files using the available file tools."
                ),
                include_usage=True,
            )

        assert isinstance(result, dict)
        returned_files = result.get("files") or {}
        assert isinstance(returned_files, dict)

        # Original files should have replacements
        assert "NewValue" in returned_files[file_a]
        assert "OldValue" not in returned_files[file_a]
        assert "NewValue" in returned_files[file_b]
        assert "OldValue" not in returned_files[file_b]

        # New file should exist
        assert "summary.txt" in returned_files
        assert returned_files["summary.txt"].strip() == "Done"

        # Verify tool usage
        history = result.get("message_history", [])
        tool_calls = [
            part["tool_name"]
            for msg in history
            if isinstance(msg, dict)
            for part in msg.get("parts", [])
            if isinstance(part, dict) and part.get("part_kind") == "tool-call"
        ]
        assert any("find_and_replace" in t for t in tool_calls)
        assert any("create_file" in t for t in tool_calls)
