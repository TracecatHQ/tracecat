from tracecat.agent.runtime import run_agent
from typing import Any, Annotated
from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry, secrets


runreveal_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="runreveal_mcp",
    grant_type="authorization_code",
)
"""RunReveal MCP OAuth2.0 credentials (Authorization Code grant).

- name: `runreveal_mcp`
- provider_id: `runreveal_mcp`
- token_name: `RUNREVEAL_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="RunReveal MCP",
    description="Use AI to interact with RunReveal.",
    display_group="RunReveal MCP",
    doc_url="https://docs.runreveal.com/ai-chat/model-context-protocol",
    namespace="tools.runreveal",
    secrets=[runreveal_mcp_oauth_secret],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
) -> dict[str, Any]:
    """Use AI to interact with RunReveal."""
    token = secrets.get(runreveal_mcp_oauth_secret.token_name)
    mcp_server_url = "https://api.runreveal.com/mcp"
    mcp_server_headers = {"Authorization": f"Bearer {token}"}
    return await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        instructions=instructions,
        mcp_server_url=mcp_server_url,
        mcp_server_headers=mcp_server_headers,
    )
