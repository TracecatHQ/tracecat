from tracecat.agent.runtime import run_agent
from typing import Any, Annotated
from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry, secrets
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS


notion_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="notion_mcp",
    grant_type="authorization_code",
)
"""Notion MCP OAuth2.0 credentials (Authorization Code grant).

- name: `notion_mcp`
- provider_id: `notion_mcp`
- token_name: `NOTION_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="Notion MCP",
    description="Use AI to interact with Notion.",
    display_group="Notion MCP",
    doc_url="https://developers.notion.com/docs/mcp",
    namespace="tools.notion",
    secrets=[notion_mcp_oauth_secret, *PYDANTIC_AI_REGISTRY_SECRETS],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
) -> dict[str, Any]:
    """Use AI to interact with Notion."""
    token = secrets.get(notion_mcp_oauth_secret.token_name)
    mcp_server_url = "https://mcp.notion.com/mcp"
    mcp_server_headers = {"Authorization": f"Bearer {token}"}
    output = await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        instructions=instructions,
        mcp_server_url=mcp_server_url,
        mcp_server_headers=mcp_server_headers,
    )
    return output.model_dump(mode="json")
