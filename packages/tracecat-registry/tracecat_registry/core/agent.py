"""AI agent with tool calling capabilities. Returns the output and full message history."""

from typing import Annotated, Any

from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry._internal.exceptions import ActionIsInterfaceError
from tracecat_registry.fields import (
    ActionType,
    AgentModel,
    AgentPreset,
    MCPIntegration,
    ModelSelection,
    TextArea,
)
from tracecat_registry.sdk.agents import OutputType

LEGACY_MODEL_FIELD_DEPRECATION_MESSAGE = "Use `model` instead."
"""Deprecation message for raw model selection fields."""
LEGACY_MODEL_FIELD_SCHEMA_EXTRA: dict[str, Any] = {
    "x-tracecat-deprecation-message": LEGACY_MODEL_FIELD_DEPRECATION_MESSAGE
}


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://docs.tracecat.com/agents/ai-agent",
    namespace="ai",
)
async def agent(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model: Annotated[
        ModelSelection | None,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("Deprecated model name. Use `model` instead."),
        Field(
            deprecated=True,
            json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA,
        ),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated model provider. Use `model` instead."),
        Field(
            deprecated=True,
            json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA,
        ),
    ] = None,
    actions: Annotated[
        list[str] | None,
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
        ActionType(multiple=True),
    ] = None,
    mcp_integrations: Annotated[
        list[str] | None,
        Doc("Saved MCP integrations to include in the agent."),
        MCPIntegration(multiple=True),
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
    session_id: Annotated[
        str | None,
        Doc(
            "Optional existing agent session ID to continue from. If provided, the session must already exist."
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
    enable_thinking: Annotated[
        bool,
        Doc("Whether to enable high thinking for agent runs."),
    ] = True,
    # Paid feature
    tool_approvals: Annotated[
        dict[str, bool] | None,
        Doc(
            "Per-tool approval overrides keyed by action name (e.g. 'core.cases.create_case'). Use true to require approval, false to allow auto-execution."
        ),
    ] = None,
) -> dict[str, Any]:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="Run agent preset",
    description="Run an AI agent using a saved agent preset.",
    display_group="AI",
    namespace="ai",
    required_entitlements=["agent_addons"],
)
async def preset_agent(
    preset: Annotated[
        str,
        Doc("Preset of the agent to run (e.g. 'security-analyst')."),
        AgentPreset(),
    ],
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    preset_version: Annotated[
        int | None,
        Doc("Optional preset version number to pin for this run."),
    ] = None,
    actions: Annotated[
        list[str] | None,
        Doc(
            "Optional override for the actions (e.g. 'tools.slack.post_message') that the agent should be allowed to call."
        ),
        ActionType(multiple=True),
    ] = None,
    instructions: Annotated[
        str | None,
        Doc(
            "Additional instructions to append to the preset instructions for this run."
        ),
        TextArea(),
    ] = None,
    session_id: Annotated[
        str | None,
        Doc(
            "Optional existing agent session ID to continue from. If provided, the session must already exist."
        ),
    ] = None,
    max_tool_calls: Annotated[
        int, Doc("Maximum number of tool calls for the agent.")
    ] = 15,
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
) -> dict[str, Any]:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="AI action",
    description="Call an LLM with a given prompt and model.",
    display_group="AI",
    doc_url="https://docs.tracecat.com/agents/ai-action",
    namespace="ai",
)
async def action(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model: Annotated[
        ModelSelection | None,
        Doc("Model to use. Pick from the list of models enabled for this workspace."),
        AgentModel(),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("Deprecated model name. Use `model` instead."),
        Field(
            deprecated=True,
            json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA,
        ),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("Deprecated model provider. Use `model` instead."),
        Field(
            deprecated=True,
            json_schema_extra=LEGACY_MODEL_FIELD_SCHEMA_EXTRA,
        ),
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
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 3,
    enable_thinking: Annotated[
        bool,
        Doc("Whether to enable high thinking for agent runs."),
    ] = True,
) -> dict[str, Any]:
    """Call an LLM with a given prompt and model (no tools)."""
    raise ActionIsInterfaceError()
