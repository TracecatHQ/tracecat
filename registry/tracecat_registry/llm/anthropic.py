from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm.anthropic import async_anthropic_call
from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry.integrations.aws_boto3 import has_usable_aws_credentials

anthropic_secret = RegistrySecret(
    name="anthropic",
    keys=["ANTHROPIC_API_KEY"],
    optional=True,
)
"""Anthropic secret.

- name: `anthropic`
- keys:
    - `ANTHROPIC_API_KEY`
"""

anthropic_bedrock_secret = RegistrySecret(
    name="anthropic_bedrock",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_REGION",
    ],
    optional=True,
)
"""AWS credentials for Claude in Amazon Bedrock.

- name: `anthropic_bedrock`
- optional_keys:
    - `AWS_ACCESS_KEY_ID`
    - `AWS_SECRET_ACCESS_KEY`
    - `AWS_REGION`
    - `AWS_SESSION_TOKEN`
    - `AWS_PROFILE`

Reference: https://github.com/anthropics/anthropic-sdk-python?tab=readme-ov-file#aws-bedrock
"""


@registry.register(
    default_title="Call Anthropic",
    description="Call an LLM via Anthropic messages API. Supports Anthropic API and Amazon Bedrock.",
    display_group="Anthropic",
    doc_url="https://docs.anthropic.com/en/api/client-sdks",
    namespace="llm.anthropic",
    secrets=[anthropic_secret, anthropic_bedrock_secret],
)
async def call(
    prompt: Annotated[
        str,
        Doc("Prompt to send to the LLM"),
    ],
    model: Annotated[
        str,
        Doc("Model to use"),
    ],
    memory: Annotated[
        list[dict[str, Any]] | None,
        Doc("Past messages to include in the conversation."),
    ] = None,
    system: Annotated[
        str | None, Doc("Insert a system message at the beginning of the conversation.")
    ] = None,
    thinking: Annotated[
        dict[str, Any] | None,
        Doc("Configuration options for a text response from the model."),
    ] = None,
    tool_choice: Annotated[
        dict[str, Any] | None,
        Doc("Configuration options for a text response from the model."),
    ] = None,
    tools: Annotated[
        list[dict[str, Any]] | None,
        Doc("Configuration options for a text response from the model."),
    ] = None,
    base_url: Annotated[
        str | None,
        Doc("Base URL for Anthropic API. Defaults to `https://api.anthropic.com`."),
    ] = None,
) -> dict[str, Any]:
    # Check if API keys or AWS credentials are provided
    api_key = secrets.get("ANTHROPIC_API_KEY")
    aws_credentials = {
        "aws_access_key_id": secrets.get("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": secrets.get("AWS_SECRET_ACCESS_KEY"),
        "aws_region": secrets.get("AWS_REGION"),
        "aws_session_token": secrets.get("AWS_SESSION_TOKEN"),
        "aws_profile": secrets.get("AWS_PROFILE"),
    }

    if not api_key and not has_usable_aws_credentials(**aws_credentials):
        raise ValueError("No API key or AWS credentials provided")

    response = await async_anthropic_call(
        prompt=prompt,
        model=model,
        memory=memory,  # type: ignore
        system=system,
        thinking=thinking,  # type: ignore
        tool_choice=tool_choice,  # type: ignore
        tools=tools,  # type: ignore
        api_key=api_key,
        base_url=base_url,
    )
    return response.model_dump()
