from typing import Annotated

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, registry
from tracecat_registry.fields import AgentModel, ModelSelection
from tracecat_registry.types import AgentOutputRead

jira_mcp_oauth_secret = RegistryOAuthSecret(
    provider_id="jira_mcp",
    grant_type="authorization_code",
)


@registry.register(
    default_title="Jira MCP",
    description="Use AI to interact with Jira via Atlassian MCP.",
    display_group="Jira MCP",
    doc_url=(
        "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/"
        "getting-started-with-the-atlassian-remote-mcp-server/"
    ),
    namespace="tools.jira",
    secrets=[jira_mcp_oauth_secret],
    deprecated="Use the 'ai.agent' action with MCP integrations instead.",
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
        Field(deprecated=True),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated. Use `model` instead."),
        Field(deprecated=True),
    ] = None,
) -> AgentOutputRead:
    raise NotImplementedError(
        "Use the 'ai.agent' action with MCP integrations instead."
    )
