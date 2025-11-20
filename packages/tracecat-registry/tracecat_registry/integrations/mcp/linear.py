from tracecat.agent.runtime import run_agent
from typing import Any, Annotated
from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry, secrets
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS


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
    secrets=[linear_mcp_oauth_secret, *PYDANTIC_AI_REGISTRY_SECRETS],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
) -> dict[str, Any]:
    """Use AI to interact with Linear."""
    token = secrets.get(linear_mcp_oauth_secret.token_name)
    mcp_server_url = "https://mcp.linear.app/mcp"
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
