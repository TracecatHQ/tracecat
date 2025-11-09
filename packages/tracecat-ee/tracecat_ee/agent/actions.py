from typing import Annotated, Any

from pydantic import BaseModel
from tracecat_registry import registry
from tracecat_registry._internal.exceptions import ActionIsInterfaceError
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS, langfuse_secret
from typing_extensions import Doc

from tracecat.agent.types import OutputType
from tracecat.registry.fields import ActionType, AgentPreset, TextArea, Yaml


@registry.register(
    default_title="Approvals AI agent",
    description="AI agent with tool calling capabilities and human-in-the-loop approval support. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, langfuse_secret],
    namespace="ai",
)
async def approvals_agent(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    actions: Annotated[
        list[str],
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
        ActionType(multiple=True),
    ],
    tool_approvals: Annotated[
        dict[str, bool] | None,
        Doc(
            "Per-tool approval overrides keyed by action name (e.g. 'core.cases.create_case'). Use true to require approval, false to allow auto-execution."
        ),
        Yaml(),
    ] = None,
    instructions: Annotated[
        str | None, Doc("Instructions for the agent."), TextArea()
    ] = None,
    output_type: Annotated[
        OutputType | None,
        Doc(
            "Output type for agent responses. Select from a list of supported types or provide a JSONSchema."
        ),
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model settings for the agent.")
    ] = None,
    max_tool_calls: Annotated[
        int, Doc("Maximum number of tool calls for the agent.")
    ] = 15,
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 3,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
) -> dict[str, Any]:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="Preset approvals AI agent",
    description="AI agent with tool calling capabilities and human-in-the-loop approval support. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, langfuse_secret],
    namespace="ai",
)
async def preset_approvals_agent(
    preset: Annotated[
        str,
        Doc("Preset of the agent to use."),
        AgentPreset(),
    ],
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
) -> dict[str, Any]:
    raise ActionIsInterfaceError()


class ApprovalsAgentActionArgs(BaseModel):
    user_prompt: str
    model_name: str
    model_provider: str
    tool_approvals: dict[str, bool] | None = None
    instructions: str | None = None
    output_type: OutputType | None = None
    model_settings: dict[str, Any] | None = None
    max_requests: int = 45
    retries: int = 3
    base_url: str | None = None
    actions: list[str] | None = None


class PresetApprovalsAgentActionArgs(BaseModel):
    preset: str
    user_prompt: str
