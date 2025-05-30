"""Test the Slack MCP host integration."""

import asyncio
import json
import os
import subprocess
import time
import uuid
import shutil
from typing import Any, AsyncGenerator, Generator

import httpx
import pytest
from dotenv import load_dotenv
from slack_sdk.web.async_client import AsyncWebClient

from tracecat_registry.integrations.mcp.hosts.slack import slackbot

# Load environment variables
load_dotenv()

# Test configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
MCP_SERVER_URL = "http://localhost:3001/sse"
MCP_SERVER_PORT = 3001

# Skip tests if Slack credentials are not available
pytestmark = pytest.mark.skipif(
    not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID,
    reason="SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set in .env file",
)


@pytest.fixture(scope="session")
def mcp_server() -> Generator[subprocess.Popen, None, None]:
    """Start the MCP server in the background for the test session."""
    # Find deno executable
    deno_executable = shutil.which("deno")

    if not deno_executable:
        # Try common installation paths if shutil.which fails
        common_paths = [
            os.path.expanduser("~/.deno/bin/deno"),
            "/usr/local/bin/deno",
            "/opt/homebrew/bin/deno",
        ]
        for path in common_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                deno_executable = path
                break

    if not deno_executable:
        raise RuntimeError(
            "Deno executable not found. Please ensure Deno is installed and in your PATH, or in a common location."
        )

    # Start the MCP server
    process = subprocess.Popen(
        [
            deno_executable,
            "run",
            "-N",
            "-R=node_modules",
            "-W=node_modules",
            "--node-modules-dir=auto",
            "jsr:@pydantic/mcp-run-python",
            "sse",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to start
    max_retries = 30
    for _ in range(max_retries):
        try:
            response = httpx.get(f"http://localhost:{MCP_SERVER_PORT}/health")
            if response.status_code == 200:
                break
        except httpx.ConnectError:
            pass
        time.sleep(0.5)
    else:
        process.terminate()
        raise RuntimeError(f"MCP server failed to start on port {MCP_SERVER_PORT}")

    yield process

    # Cleanup
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
async def slack_client() -> AsyncWebClient:
    """Create a Slack client for testing."""
    return AsyncWebClient(token=SLACK_BOT_TOKEN)


@pytest.fixture
async def bot_user_id(slack_client: AsyncWebClient) -> str:
    """Get the bot user ID."""
    response = await slack_client.auth_test()
    return response["user_id"]


@pytest.fixture
async def test_user_id(slack_client: AsyncWebClient) -> str:
    """Get a test user ID (the bot itself for testing)."""
    response = await slack_client.auth_test()
    return response["user_id"]


@pytest.fixture
def test_thread_ts() -> str:
    """Generate a unique thread timestamp for testing."""
    # Use current timestamp with microseconds to ensure uniqueness
    return f"{time.time():.6f}"


@pytest.fixture
async def cleanup_test_messages(
    slack_client: AsyncWebClient, test_thread_ts: str
) -> AsyncGenerator[None, None]:
    """Cleanup test messages after test completion."""
    yield

    # Clean up test messages
    try:
        # Get all messages in the thread
        response = await slack_client.conversations_replies(
            channel=SLACK_CHANNEL_ID,
            ts=test_thread_ts,
        )

        # Delete each message
        for message in response["messages"]:
            try:
                await slack_client.chat_delete(
                    channel=SLACK_CHANNEL_ID,
                    ts=message["ts"],
                )
            except Exception:
                # Ignore errors during cleanup
                pass
    except Exception:
        # Ignore cleanup errors
        pass


@pytest.fixture
def app_mention_event(
    bot_user_id: str, test_user_id: str, test_thread_ts: str
) -> dict[str, Any]:
    """Create a sample app mention event payload."""
    return {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "text": f"<@{bot_user_id}> How many days between 2000-01-01 and 2025-03-18?",
            "user": test_user_id,
            "ts": test_thread_ts,
            "thread_ts": test_thread_ts,
            "channel": SLACK_CHANNEL_ID,
            "blocks": [
                {
                    "type": "rich_text",
                    "block_id": "test_block",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "user", "user_id": bot_user_id},
                                {
                                    "type": "text",
                                    "text": " How many days between 2000-01-01 and 2025-03-18?",
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    }


@pytest.fixture
def tool_approval_interaction_payload(
    test_user_id: str, test_thread_ts: str
) -> dict[str, Any]:
    """Create a tool approval interaction payload."""
    tool_call_id = str(uuid.uuid4())
    return {
        "payload": json.dumps({
            "type": "block_actions",
            "user": {"id": test_user_id},
            "message": {
                "ts": f"{time.time():.6f}",
                "thread_ts": test_thread_ts,
                "blocks": [],
            },
            "actions": [
                {
                    "action_id": f"run:{tool_call_id}",
                    "value": "run",
                }
            ],
        })
    }


@pytest.mark.anyio
async def test_simple_app_mention_with_tool_call(
    mcp_server: subprocess.Popen,
    slack_client: AsyncWebClient,
    app_mention_event: dict[str, Any],
    cleanup_test_messages: None,
):
    """Test a simple app mention that triggers a tool call."""
    # Call the slackbot function
    result = await slackbot(
        trigger=app_mention_event,
        channel_id=SLACK_CHANNEL_ID,
        model_name="gpt-4o",
        model_provider="openai",
        base_url=MCP_SERVER_URL,
        timeout=10,
    )

    # Verify the result
    assert "conversation_id" in result
    assert "message_id" in result
    assert "last_result" in result

    # Give Slack time to process the message
    await asyncio.sleep(2)

    # Check if messages were posted to Slack
    thread_ts = app_mention_event["event"]["thread_ts"]
    response = await slack_client.conversations_replies(
        channel=SLACK_CHANNEL_ID,
        ts=thread_ts,
    )

    messages = response["messages"]
    assert len(messages) >= 1  # At least the initial message

    # Find the bot's response message
    bot_messages = [msg for msg in messages if "blocks" in msg and msg.get("bot_id")]
    assert len(bot_messages) >= 1

    # Check for expected blocks in the bot's message
    bot_message = bot_messages[0]
    blocks = bot_message["blocks"]

    # Should have a context block with the initial message
    context_blocks = [b for b in blocks if b.get("type") == "context"]
    assert len(context_blocks) >= 1

    # Should have tool approval buttons if a tool was called
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    if action_blocks:
        # Check for run and skip buttons
        actions = action_blocks[0]["elements"]
        assert len(actions) >= 2
        assert any("Run tool" in action.get("text", {}).get("text", "") for action in actions)
        assert any("Skip" in action.get("text", {}).get("text", "") for action in actions)


@pytest.mark.anyio
async def test_tool_approval_flow(
    mcp_server: subprocess.Popen,
    slack_client: AsyncWebClient,
    app_mention_event: dict[str, Any],
    test_user_id: str,
    cleanup_test_messages: None,
):
    """Test the complete tool approval flow."""
    thread_ts = app_mention_event["event"]["thread_ts"]

    # Step 1: Initial app mention
    result1 = await slackbot(
        trigger=app_mention_event,
        channel_id=SLACK_CHANNEL_ID,
        model_name="gpt-4o",
        model_provider="openai",
        base_url=MCP_SERVER_URL,
        timeout=10,
    )

    assert result1["last_result"]["name"] == "run_python_code"
    
    # Give Slack time to process
    await asyncio.sleep(2)

    # Get the message to find the tool call ID
    response = await slack_client.conversations_replies(
        channel=SLACK_CHANNEL_ID,
        ts=thread_ts,
    )
    
    bot_messages = [msg for msg in response["messages"] if msg.get("bot_id")]
    assert len(bot_messages) >= 1
    
    # Find the action block with buttons
    action_block = None
    for block in bot_messages[0]["blocks"]:
        if block.get("type") == "actions" and block.get("block_id", "").startswith("tool_call:"):
            action_block = block
            break
    
    assert action_block is not None
    tool_call_id = action_block["block_id"].split(":", 1)[1]
    
    # Step 2: Approve the tool call
    interaction_payload = {
        "payload": json.dumps({
            "type": "block_actions",
            "user": {"id": test_user_id},
            "message": {
                "ts": bot_messages[0]["ts"],
                "thread_ts": thread_ts,
                "blocks": bot_messages[0]["blocks"],
            },
            "actions": [
                {
                    "action_id": f"run:{tool_call_id}",
                    "value": "run",
                }
            ],
        })
    }

    result2 = await slackbot(
        trigger=interaction_payload,
        channel_id=SLACK_CHANNEL_ID,
        model_name="gpt-4o",
        model_provider="openai",
        base_url=MCP_SERVER_URL,
        timeout=10,
    )

    # Should have executed the tool and gotten a result
    assert "last_result" in result2
    
    # Give Slack time to process
    await asyncio.sleep(2)

    # Check the updated message
    response = await slack_client.conversations_replies(
        channel=SLACK_CHANNEL_ID,
        ts=thread_ts,
    )
    
    # Should have the final answer
    final_messages = [msg for msg in response["messages"] if msg.get("bot_id")]
    assert len(final_messages) >= 1
    
    # Check for the answer in the blocks
    final_blocks = final_messages[-1]["blocks"]
    text_content = []
    for block in final_blocks:
        if block.get("type") == "section" and "text" in block:
            text_content.append(block["text"]["text"])
    
    # Should contain the answer about days
    full_text = " ".join(text_content)
    assert "9,208" in full_text or "9208" in full_text  # The number of days


@pytest.mark.anyio
@pytest.mark.parametrize(
    "user_prompt,expected_tool",
    [
        ("What is 2 + 2?", "run_python_code"),
        ("Calculate the factorial of 10", "run_python_code"),
        ("What day of the week was January 1, 2000?", "run_python_code"),
    ],
)
async def test_various_prompts(
    mcp_server: subprocess.Popen,
    slack_client: AsyncWebClient,
    bot_user_id: str,
    test_user_id: str,
    cleanup_test_messages: None,
    user_prompt: str,
    expected_tool: str,
):
    """Test various prompts that should trigger tool calls."""
    thread_ts = f"{time.time():.6f}"
    
    event = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "text": f"<@{bot_user_id}> {user_prompt}",
            "user": test_user_id,
            "ts": thread_ts,
            "thread_ts": thread_ts,
            "channel": SLACK_CHANNEL_ID,
            "blocks": [
                {
                    "type": "rich_text",
                    "block_id": "test_block",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "user", "user_id": bot_user_id},
                                {"type": "text", "text": f" {user_prompt}"},
                            ],
                        }
                    ],
                }
            ],
        },
    }

    result = await slackbot(
        trigger=event,
        channel_id=SLACK_CHANNEL_ID,
        model_name="gpt-4o",
        model_provider="openai",
        base_url=MCP_SERVER_URL,
        timeout=10,
    )

    # Should request tool approval
    assert result["last_result"]["name"] == expected_tool
    
    # Verify message was posted to Slack
    await asyncio.sleep(1)
    
    response = await slack_client.conversations_replies(
        channel=SLACK_CHANNEL_ID,
        ts=thread_ts,
    )
    
    assert len(response["messages"]) >= 1


@pytest.mark.anyio
async def test_error_handling(
    slack_client: AsyncWebClient,
    bot_user_id: str,
    test_user_id: str,
    cleanup_test_messages: None,
):
    """Test error handling when MCP server is not available."""
    thread_ts = f"{time.time():.6f}"
    
    event = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "text": f"<@{bot_user_id}> test error",
            "user": test_user_id,
            "ts": thread_ts,
            "thread_ts": thread_ts,
            "channel": SLACK_CHANNEL_ID,
            "blocks": [],
        },
    }

    # Use a non-existent MCP server URL
    with pytest.raises(ConnectionError):
        await slackbot(
            trigger=event,
            channel_id=SLACK_CHANNEL_ID,
            model_name="gpt-4o",
            model_provider="openai",
            base_url="http://localhost:9999/sse",  # Non-existent server
            timeout=1,
        )

    # Check if error message was posted
    await asyncio.sleep(1)
    
    response = await slack_client.conversations_replies(
        channel=SLACK_CHANNEL_ID,
        ts=thread_ts,
    )
    
    # Should have error message
    error_messages = [
        msg for msg in response["messages"]
        if "error occurred" in msg.get("text", "").lower()
        or any("error occurred" in str(block) for block in msg.get("blocks", []))
    ]
    assert len(error_messages) >= 1    