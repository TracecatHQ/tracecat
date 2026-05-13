from typing import Annotated

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry, secrets
from tracecat_registry.context import get_context
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat_registry.core.ai import (
    LEGACY_MODEL_FIELD_SCHEMA_EXTRA,
    resolve_model_selection,
)
from tracecat_registry.fields import AgentModel, ModelSelection
from tracecat_registry.sdk.agents import AgentConfig, MCPServerConfig
from tracecat_registry.types import AgentOutputRead

sentry_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="sentry_mcp",
    grant_type="authorization_code",
)
"""Sentry MCP OAuth2.0 credentials (Authorization Code grant).

- name: `sentry_mcp`
- provider_id: `sentry_mcp`
- token_name: `SENTRY_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="Sentry MCP",
    description="Use AI to interact with Sentry.",
    display_group="Sentry MCP",
    doc_url="https://docs.sentry.io/ai/mcp/",
    namespace="tools.sentry",
    secrets=[sentry_mcp_oauth_secret, *PYDANTIC_AI_REGISTRY_SECRETS],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model: Annotated[
        ModelSelection | None,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("Deprecated. Use `model` instead."),
        Field(deprecated=True, json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated. Use `model` instead."),
        Field(deprecated=True, json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA),
    ] = None,
) -> AgentOutputRead:
    """Use AI to interact with Sentry."""
    resolved_model = resolve_model_selection(
        model=model, model_name=model_name, model_provider=model_provider
    )
    token = secrets.get(sentry_mcp_oauth_secret.token_name)
    ctx = get_context()
    result = await ctx.agents.run(
        user_prompt=user_prompt,
        config=AgentConfig(
            model_name=resolved_model.model_name,
            model_provider=resolved_model.model_provider,
            catalog_id=resolved_model.catalog_id,
            instructions=instructions,
            mcp_servers=[
                MCPServerConfig(
                    name="sentry",
                    url="https://mcp.sentry.dev/mcp",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ],
        ),
    )
    return result
