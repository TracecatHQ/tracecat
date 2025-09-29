from tracecat.agent.runtime import run_agent
from typing import Any

from tracecat_registry import RegistryOAuthSecret, registry, secrets


linear_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="linear_mcp",
    grant_type="authorization_code",
)
"""Linear MCP OAuth2.0 credentials (Authorization Code grant).

- name: `linear_mcp`
- provider_id: `linear_mcp`
- token_name: `LINEAR_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="Linear MCP",
    description="Use AI to interact with Linear.",
    display_group="Linear MCP",
    doc_url="https://linear.app/docs/mcp",
    namespace="tools.linear",
    secrets=[linear_mcp_oauth_secret],
)
async def mcp(
    user_prompt: str,
    instructions: str,
    model_name: str,
    model_provider: str,
) -> dict[str, Any]:
    """Use AI to interact with Linear."""
    token = secrets.get(linear_mcp_oauth_secret.token_name)
    mcp_server_url = "https://mcp.linear.app/mcp"
    mcp_server_headers = {"Authorization": f"Bearer {token}"}
    return await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        instructions=instructions,
        mcp_server_url=mcp_server_url,
        mcp_server_headers=mcp_server_headers,
    )
