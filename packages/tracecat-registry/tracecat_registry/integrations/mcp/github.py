from typing import Annotated

from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry, secrets
from tracecat_registry.context import get_context
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat_registry.sdk.agents import AgentConfig, MCPServerConfig
from tracecat_registry.types import AgentOutputRead

github_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="github_mcp",
    grant_type="authorization_code",
)
"""GitHub OAuth2.0 credentials (Authorization Code grant).

- name: `github_mcp`
- provider_id: `github_mcp`
- token_name: `GITHUB_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="GitHub MCP",
    description="Use AI to interact with GitHub.",
    display_group="GitHub MCP",
    doc_url="https://docs.github.com/en/copilot",
    namespace="tools.github",
    secrets=[github_mcp_oauth_secret, *PYDANTIC_AI_REGISTRY_SECRETS],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
) -> AgentOutputRead:
    """Use AI to interact with GitHub."""
    token = secrets.get(github_mcp_oauth_secret.token_name)
    ctx = get_context()
    result = await ctx.agents.run(
        user_prompt=user_prompt,
        config=AgentConfig(
            model_name=model_name,
            model_provider=model_provider,
            instructions=instructions,
            mcp_servers=[
                MCPServerConfig(
                    name="github",
                    url="https://api.githubcopilot.com/mcp",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ],
        ),
    )
    return result
