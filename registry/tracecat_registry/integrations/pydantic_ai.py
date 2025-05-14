from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import ModelMessage, UserPromptPart, TextPart

from pydantic_ai.models.openai import OpenAIModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.settings import ModelSettings

from tracecat.validation.common import json_schema_to_pydantic


from tracecat_registry.integrations.aws_boto3 import get_session


from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

openai_secret = RegistrySecret(
    name="openai",
    keys=["OPENAI_API_KEY"],
)
"""OpenAI secret.

- name: `openai`
- keys:
    - `OPENAI_API_KEY`
"""

anthropic_secret = RegistrySecret(
    name="anthropic",
    keys=["ANTHROPIC_API_KEY"],
)
"""Anthropic secret.

- name: `anthropic`
- keys:
    - `ANTHROPIC_API_KEY`
"""


aws_bedrock_secret = RegistrySecret(
    name="aws_bedrock",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_REGION",
    ],
    optional=True,
)
"""AWS credentials for Amazon Bedrock.

- name: `aws_bedrock`
- optional_keys:
    - `AWS_ACCESS_KEY_ID`
    - `AWS_SECRET_ACCESS_KEY`
    - `AWS_REGION`
    - `AWS_SESSION_TOKEN`
    - `AWS_PROFILE`
"""

# Objects should be configured via structured outputs.
SUPPORTED_OUTPUT_TYPES = {
    "bool": bool,
    "float": float,
    "int": int,
    "str": str,
    "list[bool]": list[bool],
    "list[float]": list[float],
    "list[int]": list[int],
    "list[str]": list[str],
}


def _parse_message_history(message_history: list[dict[str, Any]]) -> list[ModelMessage]:
    """Parses a list of user and assistant messages
    from a supported model provider into a list of ModelMessage objects.

    - https://ai.pydantic.dev/api/messages/
    - https://github.com/pydantic/pydantic-ai/issues/1652
    """
    messages = []
    for message in message_history:
        if message["role"] == "user":
            messages.append(UserPromptPart(content=message["content"]))
        elif message["role"] in ["assistant", "model"]:
            messages.append(TextPart(content=message["content"]))
    return messages


@registry.register(
    default_title="Call Pydantic AI agent",
    description="Call an LLM via Pydantic AI agent.",
    display_group="Pydantic AI",
    doc_url="https://ai.pydantic.dev/agents/",
    namespace="llm.pydantic_ai",
)
async def call(
    instructions: Annotated[str, Doc("Instructions to use for this agent")],
    user_prompt: Annotated[str, Doc("User prompt")],
    *,
    model_name: Annotated[str, Doc("Model to use")],
    model_provider: Annotated[
        Literal["openai", "openai_responses", "anthropic", "bedrock", "gemini"],
        Doc("Model provider to use"),
    ],
    output_type: Annotated[
        str | dict[str, Any] | None,
        Doc(
            f"Output type to use. Either JSONSchema or a supported type: {list(SUPPORTED_OUTPUT_TYPES.keys())}"
        ),
    ] = None,
    model_settings: Annotated[dict[str, Any], Doc("Model-specific settings")],
    message_history: Annotated[list[dict[str, Any]], Doc("Message history")],
    base_url: Annotated[str | None, Doc("Base URL for the model")] = None,
) -> Any:
    """Call an LLM via Pydantic AI agent."""

    match model_provider.split(":", 1):
        case ["openai", model_name]:
            model = OpenAIModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case ["openai_responses", model_name]:
            model = OpenAIResponsesModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case ["anthropic", model_name]:
            model = AnthropicModel(
                model_name=model_name,
                provider=AnthropicProvider(api_key=secrets.get("ANTHROPIC_API_KEY")),
            )
        case ["bedrock", model_name]:
            session = await get_session()
            client = session.client("bedrock-runtime")
            model = BedrockConverseModel(
                model_name=model_name,
                provider=BedrockProvider(bedrock_client=client),
            )
        case _:
            raise ValueError(f"Unsupported model: {model_name}")

    if isinstance(output_type, str):
        response_format = SUPPORTED_OUTPUT_TYPES[output_type]
    elif isinstance(output_type, dict):
        try:
            model_name = output_type.get("name") or output_type["title"]
            response_format = json_schema_to_pydantic(
                schema=output_type, name=model_name
            )
        except KeyError:
            raise ValueError(
                f"Invalid JSONSchema: {output_type}. Missing top-level `name` or `title` field."
            )
    else:
        raise ValueError(
            f"Unexpected `output_type`. Expected either JSONSchema or a supported Python type. Got {output_type}"
        )

    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=response_format,  # https://ai.pydantic.dev/output/
        model_settings=ModelSettings(**model_settings) if model_settings else None,
    )

    if message_history:
        messages = _parse_message_history(message_history)

    result: AgentRunResult[BaseModel] = await agent.run(
        user_prompt=user_prompt,
        message_history=messages,
    )

    output = result.output
    if isinstance(output, BaseModel):
        return output.model_dump()
    return output
