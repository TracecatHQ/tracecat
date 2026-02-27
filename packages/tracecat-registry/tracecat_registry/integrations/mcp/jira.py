from typing import Annotated

from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry, secrets
from tracecat_registry.context import get_context
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat_registry.sdk.agents import AgentConfig, MCPServerConfig
from tracecat_registry.types import AgentOutputRead

jira_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="jira_mcp",
    grant_type="authorization_code",
)
"""Jira MCP OAuth2.1 credentials (Authorization Code grant).

- name: `jira_mcp`
- provider_id: `jira_mcp`
- token_name: `JIRA_MCP_USER_TOKEN`
"""


@registry.register(
    default_title="Jira MCP",
    description="Use AI to interact with Jira via Atlassian MCP.",
    display_group="Jira MCP",
    doc_url=(
        "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/"
        "getting-started-with-the-atlassian-remote-mcp-server/"
    ),
    namespace="tools.jira",
    secrets=[jira_mcp_oauth_secret, *PYDANTIC_AI_REGISTRY_SECRETS],
)
async def mcp(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    instructions: Annotated[str, Doc("Instructions for the agent.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
) -> AgentOutputRead:
    """Use AI to interact with Jira through Atlassian's remote MCP server."""
    token = secrets.get(jira_mcp_oauth_secret.token_name)
    ctx = get_context()
    result = await ctx.agents.run(
        user_prompt=user_prompt,
        config=AgentConfig(
            model_name=model_name,
            model_provider=model_provider,
            instructions=instructions,
            mcp_servers=[
                MCPServerConfig(
                    name="jira",
                    url="https://mcp.atlassian.com/v1/mcp",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ],
        ),
    )
    return result
