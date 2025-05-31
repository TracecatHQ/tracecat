from pydantic import BaseModel
from pydantic_ai import Agent, Tool
from pydantic_ai.agent import AgentRunResult
import orjson
from pydantic_ai.messages import (
    ModelMessage,
    UserPromptPart,
    TextPart,
    ModelRequest,
    ModelResponse,
)

from pydantic_ai.models.openai import OpenAIModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.models.gemini import GeminiModel
from pydantic_ai.providers.google_gla import GoogleGLAProvider
from pydantic_ai.providers.google_vertex import GoogleVertexProvider
from pydantic_ai.mcp import MCPServerHTTP

from pydantic_ai.settings import ModelSettings

from tracecat.validation.common import json_schema_to_pydantic
from tracecat_registry import RegistrySecret


from tracecat_registry.integrations.aws_boto3 import get_sync_session


from typing import Annotated, Any, Literal, TypeVar

from typing_extensions import Doc

from tracecat_registry import registry, secrets


# Type variable for agent dependencies
AgentDepsT = TypeVar("AgentDepsT")


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


mcp_secret = RegistrySecret(
    name="mcp",
    optional_keys=["MCP_HTTP_HEADERS"],
    optional=True,
)
"""MCP headers.

- name: `mcp`
- optional_keys:
    - `MCP_HTTP_HEADERS`: Optional HTTP headers to send to the MCP server.
"""

anthropic_secret = RegistrySecret(
    name="anthropic",
    optional_keys=["ANTHROPIC_API_KEY"],
    optional=True,
)
"""Anthropic API key.

- name: `anthropic`
- optional_keys:
    - `ANTHROPIC_API_KEY`: Optional Anthropic API key.
"""

openai_secret = RegistrySecret(
    name="openai",
    optional_keys=["OPENAI_API_KEY"],
    optional=True,
)
"""OpenAI API key.

- name: `openai`
- optional_keys:
    - `OPENAI_API_KEY`: Optional OpenAI API key.
"""

gemini_secret = RegistrySecret(
    name="gemini",
    optional_keys=["GEMINI_API_KEY"],
    optional=True,
)
"""Gemini API key.

- name: `gemini`
- optional_keys:
    - `GEMINI_API_KEY`: Optional Gemini API key.
"""


bedrock_secret = RegistrySecret(
    name="amazon_bedrock",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
    ],
    optional=True,
)
"""Bedrock API key.

- name: `amazon_bedrock`
- optional_keys:
    - `AWS_ACCESS_KEY_ID`: Optional AWS access key ID.
    - `AWS_SECRET_ACCESS_KEY`: Optional AWS secret access key.
    - `AWS_SESSION_TOKEN`: Optional AWS session token.
    - `AWS_REGION`: Optional AWS region.
"""


PYDANTIC_AI_REGISTRY_SECRETS = [
    mcp_secret,
    anthropic_secret,
    openai_secret,
    gemini_secret,
    bedrock_secret,
]


def _parse_message_history(message_history: list[dict[str, Any]]) -> list[ModelMessage]:
    """Parses a list of user and assistant messages
    from a supported model provider into a list of ModelMessage objects.

    - https://ai.pydantic.dev/api/messages/
    - https://github.com/pydantic/pydantic-ai/issues/1652
    """
    messages = []
    for message in message_history:
        content_value: str | None = message.get("content") or message.get(
            "parts", [{}]
        )[0].get("text")
        if content_value is None:
            # Still None, raise an error or handle as appropriate
            raise ValueError(f"Message has no parsable content: {message}")

        if message["role"] == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content_value)]))
        elif message["role"] in ["assistant", "model"]:
            messages.append(ModelResponse(parts=[TextPart(content=content_value)]))
    return messages


def build_agent(
    model_name: str,
    model_provider: str,
    base_url: str | None = None,
    instructions: str | None = None,
    output_type: str | dict[str, Any] | None = None,
    model_settings: dict[str, Any] | None = None,
    mcp_servers: list[MCPServerHTTP] | None = None,
    tools: list[Tool] | None = None,
    retries: Annotated[int, Doc("Number of retries")] = 3,
    deps_type: type[AgentDepsT] | None = None,
) -> Agent[AgentDepsT, Any]:
    match model_provider:
        case "openai":
            model = OpenAIModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case "openai_responses":
            model = OpenAIResponsesModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case "anthropic":
            model = AnthropicModel(
                model_name=model_name,
                provider=AnthropicProvider(api_key=secrets.get("ANTHROPIC_API_KEY")),
            )
        case "gemini":
            model = GeminiModel(
                model_name=model_name,
                provider=GoogleGLAProvider(api_key=secrets.get("GEMINI_API_KEY")),
            )
        case "gemini_vertex":
            try:
                service_account_info = orjson.loads(
                    secrets.get("GOOGLE_API_CREDENTIALS")
                )
            except orjson.JSONDecodeError as e:
                raise ValueError(
                    "`GOOGLE_API_CREDENTIALS` is not a valid JSON string."
                ) from e
            model = GeminiModel(
                model_name=model_name,
                provider=GoogleVertexProvider(
                    service_account_info=service_account_info
                ),
            )
        case "bedrock":
            session = get_sync_session()
            client = session.client(service_name="bedrock-runtime")
            model = BedrockConverseModel(
                model_name=model_name,
                provider=BedrockProvider(bedrock_client=client),
            )
        case _:
            raise ValueError(f"Unsupported model: {model_name}")

    response_format: Any = str
    if isinstance(output_type, str):
        response_format = SUPPORTED_OUTPUT_TYPES[output_type]
    elif isinstance(output_type, dict):
        try:
            model_name_from_schema = output_type.get("name") or output_type["title"]
            response_format = json_schema_to_pydantic(
                schema=output_type, name=model_name_from_schema
            )
        except KeyError:
            raise ValueError(
                f"Invalid JSONSchema: {output_type}. Missing top-level `name` or `title` field."
            )

    mcp_servers = mcp_servers or []

    agent_kwargs = {
        "model": model,
        "instructions": instructions,
        "output_type": response_format,
        "model_settings": ModelSettings(**model_settings) if model_settings else None,
        "mcp_servers": mcp_servers,
        "tools": tools,
        "retries": retries,
    }

    # Only add deps_type if it's not None
    if deps_type is not None:
        agent_kwargs["deps_type"] = deps_type

    agent = Agent(**agent_kwargs)
    return agent


@registry.register(
    default_title="Call Pydantic AI agent",
    description="Call an LLM via Pydantic AI agent.",
    display_group="Pydantic AI",
    doc_url="https://ai.pydantic.dev/agents/",
    namespace="llm.pydantic_ai",
)
def call(
    user_prompt: Annotated[str, Doc("User prompt")],
    model_name: Annotated[str, Doc("Model to use")],
    model_provider: Annotated[
        Literal[
            "openai",
            "openai_responses",
            "anthropic",
            "bedrock",
            "gemini",
            "gemini_vertex",
        ],
        Doc("Model provider to use"),
    ],
    instructions: Annotated[
        str | None, Doc("Instructions to use for this agent")
    ] = None,
    output_type: Annotated[
        str | dict[str, Any] | None,
        Doc(
            f"Output type to use. Either JSONSchema or a supported type: {list(SUPPORTED_OUTPUT_TYPES.keys())}."
        ),
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model-specific settings")
    ] = None,
    message_history: Annotated[
        list[dict[str, Any]] | None, Doc("Message history")
    ] = None,
    retries: Annotated[int, Doc("Number of retries")] = 3,
    base_url: Annotated[str | None, Doc("Base URL for the model")] = None,
) -> Any:
    """Call an LLM via Pydantic AI agent."""
    agent = build_agent(
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        base_url=base_url,
        instructions=instructions,
        output_type=output_type,
        retries=retries,
    )
    messages: list[ModelMessage] | None = None
    if message_history:
        messages = _parse_message_history(message_history)

    result: AgentRunResult = agent.run_sync(
        user_prompt=user_prompt,
        message_history=messages,
    )

    output = result.output
    if isinstance(output, BaseModel):
        return output.model_dump()
    return output
