"""AI agent with tool calling capabilities. Returns the output and full message history."""

from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import (
    RegistrySecret,
    RegistrySecretType,
    registry,
)
from tracecat_registry._internal.exceptions import ActionIsInterfaceError
from tracecat_registry.fields import (
    ActionType,
    AgentModel,
    AgentPreset,
    ModelSelection,
    TextArea,
)
from tracecat_registry.sdk.agents import OutputType

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
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_MODEL_ID",
        "AWS_INFERENCE_PROFILE_ID",
    ],
    optional=True,
)
"""AWS Bedrock credentials.

- name: `amazon_bedrock`
- optional_keys:
    Authentication (one of):
        - `AWS_ROLE_ARN`
        - `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
        - `AWS_BEARER_TOKEN_BEDROCK`
    Optional role settings:
        - `AWS_ROLE_SESSION_NAME`: Audit session name for AssumeRole requests.
    Region:
        - `AWS_REGION`: AWS region (e.g., us-east-1)

Model selection (inference profile ID or direct model ID) is configured per
catalog entry under Organization settings → Models, not via these credentials.

Tracecat automatically injects the workspace-scoped external ID required for
cross-account AssumeRole requests.
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

azure_openai_secret = RegistrySecret(
    name="azure_openai",
    optional_keys=[
        "AZURE_API_BASE",
        "AZURE_API_VERSION",
        "AZURE_DEPLOYMENT_NAME",
        "AZURE_API_KEY",
        "AZURE_AD_TOKEN",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
    ],
    optional=True,
)
"""Azure OpenAI credentials.

- name: `azure_openai`
- optional_keys:
    - `AZURE_API_BASE`: Azure OpenAI endpoint (e.g., https://<resource>.openai.azure.com).
    - `AZURE_API_VERSION`: Azure OpenAI API version.
    - `AZURE_API_KEY`: Azure OpenAI API key. Required if not using Entra authentication.
    - `AZURE_AD_TOKEN`: Azure Entra (AD) token. Required if not using API key or client credentials.
    - `AZURE_TENANT_ID`: Azure Entra tenant ID for client-credential auth.
    - `AZURE_CLIENT_ID`: Azure Entra application client ID for client-credential auth.
    - `AZURE_CLIENT_SECRET`: Azure Entra application client secret for client-credential auth.

The deployment name is configured per catalog entry under Organization settings →
Models, not via these credentials.
"""

azure_ai_secret = RegistrySecret(
    name="azure_ai",
    optional_keys=[
        "AZURE_API_BASE",
        "AZURE_API_KEY",
        "AZURE_AD_TOKEN",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_API_VERSION",
        "AZURE_AI_MODEL_NAME",
    ],
    optional=True,
)
"""Azure AI credentials.

- name: `azure_ai`
- optional_keys:
    - `AZURE_API_BASE`: Azure AI endpoint (e.g., https://<resource>.services.ai.azure.com/anthropic).
    - `AZURE_API_KEY`: Azure AI API key. Required if not using Entra authentication.
    - `AZURE_AD_TOKEN`: Azure Entra (AD) token. Required if not using API key or client credentials.
    - `AZURE_TENANT_ID`: Azure Entra tenant ID for client-credential auth.
    - `AZURE_CLIENT_ID`: Azure Entra application client ID for client-credential auth.
    - `AZURE_CLIENT_SECRET`: Azure Entra application client secret for client-credential auth.
    - `AZURE_API_VERSION`: Optional Azure AI API version appended as the api-version query parameter.

The Azure AI model name is configured per catalog entry under Organization
settings → Models, not via these credentials.
"""

litellm_secret = RegistrySecret(
    name="litellm",
    keys=["LITELLM_BASE_URL"],
    optional=True,
)
"""LiteLLM credentials.

- name: `litellm`
- keys:
    - `LITELLM_BASE_URL`: LiteLLM base URL.
"""

PYDANTIC_AI_REGISTRY_SECRETS: list[RegistrySecretType] = [
    anthropic_secret,
    openai_secret,
    gemini_secret,
    bedrock_secret,
    custom_model_provider_secret,
    azure_openai_secret,
    azure_ai_secret,
    litellm_secret,
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
    model: Annotated[
        ModelSelection,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ],
    actions: Annotated[
        list[str] | None,
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
        ActionType(multiple=True),
    ] = None,
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
    enable_thinking: Annotated[
        bool,
        Doc("Whether to enable high thinking for agent runs."),
    ] = True,
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
    required_entitlements=["agent_addons"],
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
    preset_version: Annotated[
        int | None,
        Doc("Optional preset version number to pin for this run."),
    ] = None,
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
    model: Annotated[
        ModelSelection,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
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
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 3,
    enable_thinking: Annotated[
        bool,
        Doc("Whether to enable high thinking for agent runs."),
    ] = True,
) -> dict[str, Any]:
    """Call an LLM with a given prompt and model (no tools)."""
    raise ActionIsInterfaceError()
