"""AI agent with tool calling capabilities. Returns the output and full message history."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

import httpx

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry._internal.tls import TemporaryClientCertificate
from tracecat.agent.models import AgentConfig, OutputType
from tracecat.agent.runtime import run_agent, run_agent_sync
from tracecat.agent.factory import build_agent


from tracecat.registry.fields import ActionType, TextArea
from typing_extensions import Doc

T = TypeVar("T")


async def _with_optional_tls_client(
    callback: Callable[[httpx.AsyncClient | None], Awaitable[T]],
) -> T:
    """Execute a coroutine with an optional TLS-configured HTTP client."""

    client_cert_str = secrets.get_or_default("SSL_CLIENT_CERT")
    client_key_str = secrets.get_or_default("SSL_CLIENT_KEY")
    client_key_password = secrets.get_or_default("SSL_CLIENT_PASSWORD")

    with TemporaryClientCertificate(
        client_cert_str=client_cert_str,
        client_key_str=client_key_str,
        client_key_password=client_key_password,
    ) as cert_for_httpx:
        if cert_for_httpx:
            async with httpx.AsyncClient(cert=cert_for_httpx) as http_client:
                return await callback(http_client)
        return await callback(None)


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

ssl_secret = RegistrySecret(
    name="ssl",
    optional_keys=["SSL_CLIENT_CERT", "SSL_CLIENT_KEY", "SSL_CLIENT_PASSWORD"],
    optional=True,
)
"""AI TLS certificate configuration.

By default, AI actions rely on the platform CA bundle. This optional secret
allows providing a client certificate pair for providers that require mutual
TLS or custom gateways.

- name: `ssl`
- optional keys:
    - `SSL_CLIENT_CERT`
    - `SSL_CLIENT_KEY`
    - `SSL_CLIENT_PASSWORD`

Note: `SSL_CLIENT_CERT` and `SSL_CLIENT_KEY` should contain the PEM encoded
certificate and key respectively. `SSL_CLIENT_PASSWORD` is optional.
"""

langfuse_secret = RegistrySecret(
    name="langfuse",
    optional_keys=[
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ],
    optional=True,
)
"""Langfuse observability configuration.

- name: `langfuse`
- optional_keys:
    - `LANGFUSE_HOST`: Optional Langfuse host URL.
    - `LANGFUSE_PUBLIC_KEY`: Optional Langfuse public key.
    - `LANGFUSE_SECRET_KEY`: Optional Langfuse secret key.
"""

PYDANTIC_AI_REGISTRY_SECRETS = [
    anthropic_secret,
    openai_secret,
    gemini_secret,
    bedrock_secret,
    custom_model_provider_secret,
    ssl_secret,
]


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, langfuse_secret],
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
) -> dict[str, Any]:
    async def _run(http_client: httpx.AsyncClient | None):
        output = await run_agent(
            user_prompt=user_prompt,
            model_name=model_name,
            model_provider=model_provider,
            actions=actions,
            instructions=instructions,
            output_type=output_type,
            model_settings=model_settings,
            max_tool_calls=max_tool_calls,
            max_requests=max_requests,
            base_url=base_url,
            retries=retries,
            http_client=http_client,
        )
        return output

    output = await _with_optional_tls_client(_run)
    return output.model_dump(mode="json")


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
) -> Any:
    async def _run(http_client: httpx.AsyncClient | None):
        agent = await build_agent(
            AgentConfig(
                model_name=model_name,
                model_provider=model_provider,
                instructions=instructions,
                output_type=output_type,
                model_settings=model_settings,
                retries=retries,
                base_url=base_url,
                http_client=http_client,
            )
        )
        result = await run_agent_sync(agent, user_prompt, max_requests=max_requests)
        return result

    agent_output = await _with_optional_tls_client(_run)
    return agent_output.model_dump()
