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

wiz_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="wiz_mcp",
    grant_type="authorization_code",
)
"""Wiz MCP OAuth2.1 credentials (Authorization Code grant).

- name: `wiz_mcp`
- provider_id: `wiz_mcp`
- token_name: `WIZ_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="Wiz MCP",
    description="Use AI to interact with Wiz.",
    display_group="Wiz MCP",
    doc_url="https://www.wiz.io/blog/introducing-mcp-server-for-wiz",
    namespace="tools.wiz",
    secrets=[wiz_mcp_oauth_secret],
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
    """Use AI to interact with Wiz."""
    raise ActionIsInterfaceError()
