"""AI agent with tool calling capabilities. Returns the output and full message history."""

from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import (
    RegistrySecret,
    RegistrySecretType,
    registry,
    types,
)
from tracecat_registry._internal.exceptions import ActionIsInterfaceError
from tracecat_registry.context import get_context
from tracecat_registry.fields import ActionType, AgentPreset, TextArea
from tracecat_registry.sdk.agents import AgentConfig, OutputType

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
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_SESSION_TOKEN",
    ],
    optional=True,
)
"""AWS credentials.

- name: `amazon_bedrock`
- optional_keys:
    Either:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_REGION`
    Or:
        - `AWS_PROFILE`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME` (optional)
    Or:
        - `AWS_SESSION_TOKEN`
"""


google_secret = RegistrySecret(
    name="google",
    optional_keys=["GOOGLE_API_CREDENTIALS"],
    optional=True,
)
"""Google API credentials.

- name: `google`
- optional_keys:
    - `GOOGLE_API_CREDENTIALS`: Optional Google API credentials.

Note: `GOOGLE_API_CREDENTIALS` should be a JSON string of the service account credentials.
"""


custom_model_provider_secret = RegistrySecret(
    name="custom-model-provider",
    optional_keys=[
        "CUSTOM_MODEL_PROVIDER_API_KEY",
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME",
        "CUSTOM_MODEL_PROVIDER_BASE_URL",
    ],
    optional=True,
)
"""Custom model provider credentials.

- name: `custom-model-provider`
- optional_keys:
    - `CUSTOM_MODEL_PROVIDER_API_KEY`: Optional custom model provider API key.
    - `CUSTOM_MODEL_PROVIDER_MODEL_NAME`: Optional custom model provider model name.
    - `CUSTOM_MODEL_PROVIDER_BASE_URL`: Optional custom model provider base URL.
"""

PYDANTIC_AI_REGISTRY_SECRETS: list[RegistrySecretType] = [
    anthropic_secret,
    openai_secret,
    gemini_secret,
    bedrock_secret,
    custom_model_provider_secret,
]


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
    namespace="ai",
)
async def agent(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    actions: Annotated[
        list[str],
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
        ActionType(multiple=True),
    ],
    instructions: Annotated[
        str | None, Doc("Instructions for the agent."), TextArea()
    ] = None,
    output_type: Annotated[
        OutputType | None,
        Doc(
            "Output type for agent responses. Select from a list of supported types or provide a JSONSchema."
        ),
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model settings for the agent.")
    ] = None,
    max_tool_calls: Annotated[
        int, Doc("Maximum number of tool calls for the agent.")
    ] = 15,
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 3,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
    # Paid feature
    tool_approvals: Annotated[
        dict[str, bool] | None,
        Doc(
            "Per-tool approval overrides keyed by action name (e.g. 'core.cases.create_case'). Use true to require approval, false to allow auto-execution."
        ),
    ] = None,
) -> dict[str, Any]:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="Run agent preset",
    description="Run an AI agent using a saved agent preset.",
    display_group="AI",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
    namespace="ai",
)
async def preset_agent(
    preset: Annotated[
        str,
        Doc("Preset of the agent to run (e.g. 'security-analyst')."),
        AgentPreset(),
    ],
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    actions: Annotated[
        list[str] | None,
        Doc(
            "Optional override for the actions (e.g. 'tools.slack.post_message') that the agent should be allowed to call."
        ),
        ActionType(multiple=True),
    ] = None,
    instructions: Annotated[
        str | None,
        Doc(
            "Additional instructions to append to the preset instructions for this run."
        ),
        TextArea(),
    ] = None,
    max_tool_calls: Annotated[
        int, Doc("Maximum number of tool calls for the agent.")
    ] = 15,
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
) -> dict[str, Any]:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="AI action",
    description="Call an LLM with a given prompt and model.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    namespace="ai",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
)
async def action(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    instructions: Annotated[
        str | None, Doc("Instructions for the agent."), TextArea()
    ] = None,
    output_type: Annotated[
        OutputType | None,
        Doc(
            "Output type for agent responses. Select from a list of supported types or provide a JSONSchema."
        ),
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model settings for the agent.")
    ] = None,
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 20,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 6,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
) -> types.AgentOutputRead:
    """Call an LLM with a given prompt and model (no tools)."""
    ctx = get_context()
    return await ctx.agents.run(
        user_prompt=user_prompt,
        config=AgentConfig(
            model_name=model_name,
            model_provider=model_provider,
            instructions=instructions,
            output_type=output_type,
            model_settings=model_settings,
            retries=retries,
            base_url=base_url,
        ),
        max_requests=max_requests,
    )
