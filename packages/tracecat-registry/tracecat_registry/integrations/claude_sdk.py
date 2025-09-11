"""Claude code SDK integration."""

from typing import Annotated, Any
from dataclasses import asdict

from claude_code_sdk import ClaudeSDKClient, ClaudeCodeOptions
from claude_code_sdk.types import (
    Message,
)
from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.integrations.pydantic_ai import (
    anthropic_secret,
    bedrock_secret,
)


@registry.register(
    default_title="Run Claude code",
    description="Run Claude code agent with tool calling capabilities",
    display_group="Claude code",
    doc_url="https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-python",
    namespace="llm.claude_code",
    secrets=[anthropic_secret, bedrock_secret],
)
async def agent(
    user_prompt: Annotated[str, Doc("User prompt")],
    model_name: Annotated[str, Doc("Model to use (e.g., 'claude-3-5-sonnet-latest')")],
    system_prompt: Annotated[str | None, Doc("System prompt to use")] = None,
) -> list[dict[str, Any]]:
    """Run Claude code agent."""

    messages = []
    async with ClaudeSDKClient(
        options=ClaudeCodeOptions(
            model=model_name,
            system_prompt=system_prompt,
        )
    ) as client:
        # Send the query
        await client.query(user_prompt)

        # Collect all messages
        messages: list[Message] = []
        async for message in client.receive_messages():
            messages.append(message)

    return [asdict(message) for message in messages]
