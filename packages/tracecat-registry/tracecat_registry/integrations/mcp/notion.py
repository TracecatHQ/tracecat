from tracecat.agent.runtime import run_agent
from typing import Any

from tracecat_registry import RegistryOAuthSecret, registry, secrets


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
    secrets=[notion_mcp_oauth_secret],
)
async def mcp(
    user_prompt: str,
    instructions: str,
    model_name: str,
    model_provider: str,
) -> dict[str, Any]:
    """Use AI to interact with Notion."""
    token = secrets.get(notion_mcp_oauth_secret.token_name)
    mcp_server_url = "https://mcp.notion.com/mcp"
    mcp_server_headers = {"Authorization": f"Bearer {token}"}
    return await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        instructions=instructions,
        mcp_server_url=mcp_server_url,
        mcp_server_headers=mcp_server_headers,
    )
