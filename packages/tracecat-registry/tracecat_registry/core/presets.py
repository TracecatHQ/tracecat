"""Core registry UDFs for agent presets."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.context import get_context

OutputTypeLiteral = Literal[
    "bool",
    "float",
    "int",
    "str",
    "list[bool]",
    "list[float]",
    "list[int]",
    "list[str]",
]


@registry.register(
    default_title="Create agent preset",
    display_group="Agent Presets",
    description="Create a new reusable agent preset configuration. Agent presets define LLM model settings, system instructions, available tools (actions), and output format. Once created, presets can be referenced by slug to run agents with consistent configurations.",
    namespace="ai.agent",
)
async def create_preset(
    name: Annotated[
        str,
        Doc(
            "A human-readable name for the agent preset (e.g., 'Security Analyst', 'Data Processor')."
        ),
    ],
    model_name: Annotated[
        str,
        Doc(
            "The LLM model name to use (e.g., 'gpt-4', 'claude-3-opus', 'gemini-pro')."
        ),
    ],
    model_provider: Annotated[
        str,
        Doc("The LLM provider identifier (e.g., 'openai', 'anthropic', 'google')."),
    ],
    slug: Annotated[
        str | None,
        Doc(
            "A URL-friendly identifier for the preset. If not provided, will be auto-generated from the name (e.g., 'security-analyst')."
        ),
    ] = None,
    description: Annotated[
        str | None,
        Doc("A brief description of what this agent preset is used for."),
    ] = None,
    instructions: Annotated[
        str | None,
        Doc(
            "System instructions/prompt that define the agent's behavior, role, and how it should respond. This is the main prompt that guides the agent's actions."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        Doc(
            "Custom API endpoint URL for the model. Only needed if using a custom or self-hosted model endpoint. Leave empty for standard provider APIs."
        ),
    ] = None,
    output_type: Annotated[
        OutputTypeLiteral | dict[str, Any] | None,
        Doc(
            "The expected output format. Can be a simple type string ('bool', 'str', 'int', 'float', 'list[bool]', 'list[str]', etc.) or a JSON schema dictionary for structured output. If a JSON schema, it should be a valid JSON Schema object defining the structure of the expected response."
        ),
    ] = None,
    actions: Annotated[
        list[str] | None,
        Doc(
            "List of action identifiers that the agent can use as tools. Format: 'namespace.action' (e.g., ['core.cases.create_case', 'core.cases.update_case']). These actions become available to the agent during execution."
        ),
    ] = None,
) -> dict[str, Any]:
    # Build kwargs, only including non-None values
    kwargs: dict[str, Any] = {
        "name": name,
        "model_name": model_name,
        "model_provider": model_provider,
    }
    if slug is not None:
        kwargs["slug"] = slug
    if description is not None:
        kwargs["description"] = description
    if instructions is not None:
        kwargs["instructions"] = instructions
    if base_url is not None:
        kwargs["base_url"] = base_url
    if output_type is not None:
        kwargs["output_type"] = output_type
    if actions is not None:
        kwargs["actions"] = actions

    return await get_context().agents.create_preset(**kwargs)


@registry.register(
    default_title="Get agent preset",
    display_group="Agent Presets",
    description="Retrieve the full configuration details of an agent preset by its slug identifier. Returns all preset settings including model configuration, instructions, actions, and output type.",
    namespace="ai.agent",
)
async def get_preset(
    slug: Annotated[
        str,
        Doc(
            "The slug identifier of the preset to retrieve (e.g., 'security-analyst')."
        ),
    ],
) -> dict[str, Any]:
    return await get_context().agents.get_preset(slug)


@registry.register(
    default_title="List agent presets",
    display_group="Agent Presets",
    description="List all agent presets available in the current workspace. Returns presets ordered by most recently created first.",
    namespace="ai.agent",
)
async def list_presets() -> list[dict[str, Any]]:
    return await get_context().agents.list_presets()


@registry.register(
    default_title="Update agent preset",
    display_group="Agent Presets",
    description="Update one or more fields of an existing agent preset. Only provide the fields you want to change. The preset is identified by its slug.",
    namespace="ai.agent",
)
async def update_preset(
    slug: Annotated[
        str,
        Doc("The slug identifier of the preset to update (e.g., 'security-analyst')."),
    ],
    name: Annotated[
        str | None,
        Doc("The updated human-readable name for the agent preset."),
    ] = None,
    model_name: Annotated[
        str | None,
        Doc("The updated LLM model name (e.g., 'gpt-4', 'claude-3-opus')."),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc("The updated LLM provider identifier (e.g., 'openai', 'anthropic')."),
    ] = None,
    new_slug: Annotated[
        str | None,
        Doc(
            "The updated slug identifier. Use this to change the preset's slug (e.g., 'security-analyst-v2')."
        ),
    ] = None,
    description: Annotated[
        str | None,
        Doc("The updated description of what this agent preset is used for."),
    ] = None,
    instructions: Annotated[
        str | None,
        Doc(
            "The updated system instructions/prompt that define the agent's behavior and role."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        Doc(
            "The updated custom API endpoint URL for the model. Only needed for custom or self-hosted model endpoints."
        ),
    ] = None,
    output_type: Annotated[
        OutputTypeLiteral | dict[str, Any] | None,
        Doc(
            "The updated output format. Can be a simple type string ('bool', 'str', 'int', 'float', 'list[bool]', 'list[str]', etc.) or a JSON schema dictionary for structured output."
        ),
    ] = None,
    actions: Annotated[
        list[str] | None,
        Doc(
            "The updated list of action identifiers that the agent can use as tools. Format: 'namespace.action' (e.g., ['core.cases.create_case'])."
        ),
    ] = None,
) -> dict[str, Any]:
    # Build kwargs, only including non-None values
    kwargs: dict[str, Any] = {}
    if name is not None:
        kwargs["name"] = name
    if model_name is not None:
        kwargs["model_name"] = model_name
    if model_provider is not None:
        kwargs["model_provider"] = model_provider
    if new_slug is not None:
        kwargs["new_slug"] = new_slug
    if description is not None:
        kwargs["description"] = description
    if instructions is not None:
        kwargs["instructions"] = instructions
    if base_url is not None:
        kwargs["base_url"] = base_url
    if output_type is not None:
        kwargs["output_type"] = output_type
    if actions is not None:
        kwargs["actions"] = actions

    return await get_context().agents.update_preset(slug, **kwargs)


@registry.register(
    default_title="Delete agent preset",
    display_group="Agent Presets",
    description="Permanently delete an agent preset from the workspace. The preset is identified by its slug. This action cannot be undone.",
    namespace="ai.agent",
)
async def delete_preset(
    slug: Annotated[
        str,
        Doc("The slug identifier of the preset to delete (e.g., 'security-analyst')."),
    ],
) -> None:
    await get_context().agents.delete_preset(slug)
