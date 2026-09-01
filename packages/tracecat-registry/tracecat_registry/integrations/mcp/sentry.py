from typing import Annotated

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, RegistryOAuthSecret, registry
from tracecat_registry.core.ai import (
    LEGACY_MODEL_FIELD_SCHEMA_EXTRA,
    MCP_MODEL_NAME_FIELD_DOC,
    MCP_MODEL_PROVIDER_FIELD_DOC,
    MCP_MODEL_SELECTION_FIELD_DOC,
)
from tracecat_registry.fields import AgentModel, ModelSelection
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
    secrets=[sentry_mcp_oauth_secret],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model: Annotated[
        ModelSelection | None,
        Doc(MCP_MODEL_SELECTION_FIELD_DOC),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc(MCP_MODEL_NAME_FIELD_DOC),
        Field(deprecated=True, json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc(MCP_MODEL_PROVIDER_FIELD_DOC),
        Field(deprecated=True, json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA),
    ] = None,
) -> AgentOutputRead:
    """Use AI to interact with Sentry."""
    raise ActionIsInterfaceError()
