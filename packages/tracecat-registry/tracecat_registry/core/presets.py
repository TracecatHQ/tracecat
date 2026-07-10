"""Core registry UDFs for agent presets."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import ctx, registry

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


def _preset_skills(preset: dict[str, Any]) -> list[dict[str, Any]]:
    return list(preset.get("skills") or [])


def _skill_binding(skill_id: uuid.UUID, skill_version_id: uuid.UUID) -> dict[str, str]:
    return {
        "skill_id": str(skill_id),
        "skill_version_id": str(skill_version_id),
    }


def _skill_index(skills: list[dict[str, Any]], skill_id: uuid.UUID) -> int | None:
    target = str(skill_id)
    for index, skill in enumerate(skills):
        if str(skill.get("skill_id")) == target:
            return index
    return None


def _agents_config(preset: dict[str, Any]) -> dict[str, Any]:
    agents = dict(preset.get("agents") or {})
    agents["subagents"] = list(agents.get("subagents") or [])
    agents["enabled"] = bool(agents.get("enabled"))
    return agents


def _subagent_ref(
    *,
    preset: str,
    preset_version: int | None = None,
    name: str | None = None,
    description: str | None = None,
    max_turns: int | None = None,
) -> dict[str, Any]:
    ref: dict[str, Any] = {"preset": preset}
    if preset_version is not None:
        ref["preset_version"] = preset_version
    if name is not None:
        ref["name"] = name
    if description is not None:
        ref["description"] = description
    if max_turns is not None:
        ref["max_turns"] = max_turns
    return ref


def _subagent_alias(subagent: dict[str, Any]) -> str:
    name = subagent.get("name")
    if name:
        return str(name)
    return str(subagent.get("preset"))


def _subagent_index(subagents: list[dict[str, Any]], alias: str) -> int | None:
    for index, subagent in enumerate(subagents):
        if _subagent_alias(subagent) == alias:
            return index
    return None


@registry.register(
    default_title="Create agent preset",
    display_group="Agent Presets",
    description="Create a new reusable agent preset configuration. Agent presets define LLM model settings, system instructions, available tools (actions), and output format. Once created, presets can be referenced by slug to run agents with consistent configurations.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def create_preset(
    name: Annotated[
        str,
        Doc(
            "A human-readable name for the agent preset (e.g., 'Security Analyst', 'Data Processor')."
        ),
    ],
    model_name: Annotated[
        str | None,
        Doc(
            "Deprecated legacy model name retained for backward compatibility. "
            "Prefer `catalog_id`, which is the canonical model selector. If omitted, "
            "the workspace default model is used."
        ),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc(
            "Deprecated legacy model provider retained for backward compatibility. "
            "Prefer `catalog_id`, which is the canonical model selector. If omitted, "
            "the workspace default provider is used."
        ),
    ] = None,
    catalog_id: Annotated[
        str | None,
        Doc(
            "Canonical model catalog row ID backing this preset. Prefer this over "
            "legacy `model_name` and `model_provider`."
        ),
    ] = None,
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
    namespaces: Annotated[
        list[str] | None,
        Doc("Optional namespaces to scope dynamic tool discovery."),
    ] = None,
    tool_approvals: Annotated[
        dict[str, bool] | None,
        Doc(
            "Per-tool approval map. True means the tool requires manual approval; false means auto-run."
        ),
    ] = None,
    mcp_integrations: Annotated[
        list[str] | None,
        Doc("Optional workspace MCP integration IDs to expose to the preset."),
    ] = None,
    agents: Annotated[
        dict[str, Any] | None,
        Doc("Optional subagent configuration with `enabled` and `subagents` fields."),
    ] = None,
    retries: Annotated[
        int | None,
        Doc("Maximum retry count for the preset."),
    ] = None,
    enable_thinking: Annotated[
        bool | None,
        Doc("Whether to enable model thinking where supported."),
    ] = None,
    enable_internet_access: Annotated[
        bool | None,
        Doc("Whether to enable internet access for the preset runtime."),
    ] = None,
    skills: Annotated[
        list[dict[str, Any]] | None,
        Doc("Optional skill bindings for the preset."),
    ] = None,
) -> dict[str, Any]:
    # Build kwargs, only including non-None values
    kwargs: dict[str, Any] = {"name": name}
    # Deprecated legacy fields retained for backward compatibility.
    # catalog_id is the canonical model selector for new callers.
    if model_name is not None:
        kwargs["model_name"] = model_name
    if model_provider is not None:
        kwargs["model_provider"] = model_provider
    if catalog_id is not None:
        kwargs["catalog_id"] = catalog_id
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
    if namespaces is not None:
        kwargs["namespaces"] = namespaces
    if tool_approvals is not None:
        kwargs["tool_approvals"] = tool_approvals
    if mcp_integrations is not None:
        kwargs["mcp_integrations"] = mcp_integrations
    if agents is not None:
        kwargs["agents"] = agents
    if retries is not None:
        kwargs["retries"] = retries
    if enable_thinking is not None:
        kwargs["enable_thinking"] = enable_thinking
    if enable_internet_access is not None:
        kwargs["enable_internet_access"] = enable_internet_access
    if skills is not None:
        kwargs["skills"] = skills

    return await ctx.agents.aio.create_preset(**kwargs)


@registry.register(
    default_title="Get agent preset",
    display_group="Agent Presets",
    description="Retrieve the full configuration details of an agent preset by its slug identifier. Returns all preset settings including model configuration, instructions, actions, and output type.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def get_preset(
    slug: Annotated[
        str,
        Doc(
            "The slug identifier of the preset to retrieve (e.g., 'security-analyst')."
        ),
    ],
) -> dict[str, Any]:
    return await ctx.agents.aio.get_preset(slug)


@registry.register(
    default_title="List agent presets",
    display_group="Agent Presets",
    description="List all agent presets available in the current workspace. Returns presets ordered by most recently created first.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def list_presets() -> list[dict[str, Any]]:
    return await ctx.agents.aio.list_presets()


@registry.register(
    default_title="List preset skills",
    display_group="Agent Presets",
    description="List the published skill versions attached to an agent preset.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def list_preset_skills(
    slug: Annotated[str, Doc("The slug identifier of the preset.")],
) -> list[dict[str, Any]]:
    preset = await ctx.agents.aio.get_preset(slug)
    return _preset_skills(preset)


@registry.register(
    default_title="Add preset skill",
    display_group="Agent Presets",
    description="Attach a published skill version to an agent preset.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def add_preset_skill(
    slug: Annotated[str, Doc("The slug identifier of the preset to update.")],
    skill_id: Annotated[uuid.UUID, Doc("Canonical skill UUID to attach.")],
    skill_version_id: Annotated[
        uuid.UUID, Doc("Published skill version UUID to attach.")
    ],
    replace_existing: Annotated[
        bool,
        Doc("Replace the existing binding for this skill if it is already attached."),
    ] = False,
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    skills = _preset_skills(preset)
    binding = _skill_binding(skill_id, skill_version_id)
    index = _skill_index(skills, skill_id)
    if index is None:
        skills.append(binding)
    elif replace_existing:
        skills[index] = binding
    else:
        raise ValueError(
            f"Skill '{skill_id}' is already attached to preset '{slug}'. "
            "Use replace_existing=true to change its version."
        )
    return await ctx.agents.aio.update_preset(slug, skills=skills)


@registry.register(
    default_title="Update preset skill",
    display_group="Agent Presets",
    description="Change the published skill version attached to an agent preset.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def update_preset_skill(
    slug: Annotated[str, Doc("The slug identifier of the preset to update.")],
    skill_id: Annotated[uuid.UUID, Doc("Canonical skill UUID already attached.")],
    skill_version_id: Annotated[
        uuid.UUID, Doc("New published skill version UUID to attach.")
    ],
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    skills = _preset_skills(preset)
    index = _skill_index(skills, skill_id)
    if index is None:
        raise ValueError(f"Skill '{skill_id}' is not attached to preset '{slug}'.")
    skills[index] = _skill_binding(skill_id, skill_version_id)
    return await ctx.agents.aio.update_preset(slug, skills=skills)


@registry.register(
    default_title="Remove preset skill",
    display_group="Agent Presets",
    description="Detach a skill from an agent preset.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def remove_preset_skill(
    slug: Annotated[str, Doc("The slug identifier of the preset to update.")],
    skill_id: Annotated[uuid.UUID, Doc("Canonical skill UUID to detach.")],
    allow_missing: Annotated[
        bool,
        Doc("Return the unchanged preset instead of failing when the skill is absent."),
    ] = False,
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    skills = _preset_skills(preset)
    index = _skill_index(skills, skill_id)
    if index is None:
        if allow_missing:
            return preset
        raise ValueError(f"Skill '{skill_id}' is not attached to preset '{slug}'.")
    del skills[index]
    return await ctx.agents.aio.update_preset(slug, skills=skills)


@registry.register(
    default_title="List preset subagents",
    display_group="Agent Subagents",
    description="List preset-backed subagent bindings configured on an agent preset.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def list_preset_subagents(
    slug: Annotated[str, Doc("The slug identifier of the parent preset.")],
) -> list[dict[str, Any]]:
    preset = await ctx.agents.aio.get_preset(slug)
    return _agents_config(preset)["subagents"]


@registry.register(
    default_title="Get preset subagent",
    display_group="Agent Subagents",
    description="Get one preset-backed subagent binding by alias.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def get_preset_subagent(
    slug: Annotated[str, Doc("The slug identifier of the parent preset.")],
    alias: Annotated[
        str,
        Doc(
            "Subagent alias. This is `name` when set, otherwise the child preset slug."
        ),
    ],
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    subagents = _agents_config(preset)["subagents"]
    index = _subagent_index(subagents, alias)
    if index is None:
        raise ValueError(f"Subagent '{alias}' is not attached to preset '{slug}'.")
    return subagents[index]


@registry.register(
    default_title="Add preset subagent",
    display_group="Agent Subagents",
    description="Attach a child preset as a subagent, optionally pinned to a preset version.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def add_preset_subagent(
    slug: Annotated[str, Doc("The slug identifier of the parent preset to update.")],
    subagent_preset: Annotated[str, Doc("Child preset slug to attach as a subagent.")],
    preset_version: Annotated[
        int | None,
        Doc(
            "Optional child preset version number to pin. Omit to use the current version."
        ),
    ] = None,
    name: Annotated[
        str | None,
        Doc("Optional runtime alias. Defaults to the child preset slug."),
    ] = None,
    description: Annotated[
        str | None,
        Doc(
            "Optional description shown to the root agent for when to use this subagent."
        ),
    ] = None,
    max_turns: Annotated[
        int | None,
        Doc("Optional maximum turns for this subagent."),
    ] = None,
    replace_existing: Annotated[
        bool,
        Doc("Replace an existing subagent with the same alias."),
    ] = False,
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    agents = _agents_config(preset)
    subagents = agents["subagents"]
    ref = _subagent_ref(
        preset=subagent_preset,
        preset_version=preset_version,
        name=name,
        description=description,
        max_turns=max_turns,
    )
    alias = _subagent_alias(ref)
    index = _subagent_index(subagents, alias)
    if index is None:
        subagents.append(ref)
    elif replace_existing:
        subagents[index] = ref
    else:
        raise ValueError(
            f"Subagent '{alias}' is already attached to preset '{slug}'. "
            "Use replace_existing=true to change it."
        )
    agents["enabled"] = True
    return await ctx.agents.aio.update_preset(slug, agents=agents)


@registry.register(
    default_title="Update preset subagent",
    display_group="Agent Subagents",
    description="Update a preset-backed subagent binding, including its pinned version.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def update_preset_subagent(
    slug: Annotated[str, Doc("The slug identifier of the parent preset to update.")],
    alias: Annotated[
        str,
        Doc(
            "Current subagent alias. This is `name` when set, otherwise the child preset slug."
        ),
    ],
    subagent_preset: Annotated[
        str | None,
        Doc("Updated child preset slug. Omit to keep the existing child preset."),
    ] = None,
    preset_version: Annotated[
        int | None,
        Doc(
            "Updated child preset version number. Omit to keep the existing version pin."
        ),
    ] = None,
    name: Annotated[
        str | None,
        Doc("Updated runtime alias. Omit to keep the existing alias."),
    ] = None,
    description: Annotated[
        str | None,
        Doc("Updated subagent description. Omit to keep the existing description."),
    ] = None,
    max_turns: Annotated[
        int | None,
        Doc("Updated maximum turns. Omit to keep the existing value."),
    ] = None,
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    agents = _agents_config(preset)
    subagents = agents["subagents"]
    index = _subagent_index(subagents, alias)
    if index is None:
        raise ValueError(f"Subagent '{alias}' is not attached to preset '{slug}'.")
    ref = dict(subagents[index])
    if subagent_preset is not None:
        ref["preset"] = subagent_preset
    if preset_version is not None:
        ref["preset_version"] = preset_version
    if name is not None:
        ref["name"] = name
    if description is not None:
        ref["description"] = description
    if max_turns is not None:
        ref["max_turns"] = max_turns
    new_alias = _subagent_alias(ref)
    existing_index = _subagent_index(subagents, new_alias)
    if existing_index is not None and existing_index != index:
        raise ValueError(
            f"Cannot rename subagent '{alias}' to '{new_alias}' because that alias already exists."
        )
    subagents[index] = ref
    agents["enabled"] = True
    return await ctx.agents.aio.update_preset(slug, agents=agents)


@registry.register(
    default_title="Remove preset subagent",
    display_group="Agent Subagents",
    description="Detach a preset-backed subagent from an agent preset.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def remove_preset_subagent(
    slug: Annotated[str, Doc("The slug identifier of the parent preset to update.")],
    alias: Annotated[
        str,
        Doc(
            "Subagent alias. This is `name` when set, otherwise the child preset slug."
        ),
    ],
    allow_missing: Annotated[
        bool,
        Doc(
            "Return the unchanged preset instead of failing when the subagent is absent."
        ),
    ] = False,
) -> dict[str, Any]:
    preset = await ctx.agents.aio.get_preset(slug)
    agents = _agents_config(preset)
    subagents = agents["subagents"]
    index = _subagent_index(subagents, alias)
    if index is None:
        if allow_missing:
            return preset
        raise ValueError(f"Subagent '{alias}' is not attached to preset '{slug}'.")
    del subagents[index]
    agents["enabled"] = bool(subagents)
    return await ctx.agents.aio.update_preset(slug, agents=agents)


@registry.register(
    default_title="Update agent preset",
    display_group="Agent Presets",
    description="Update one or more fields of an existing agent preset. Only provide the fields you want to change. The preset is identified by its slug.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
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
        Doc(
            "Deprecated legacy model name retained for backward compatibility. "
            "Prefer `catalog_id`, which is the canonical model selector."
        ),
    ] = None,
    model_provider: Annotated[
        str | None,
        Doc(
            "Deprecated legacy model provider retained for backward compatibility. "
            "Prefer `catalog_id`, which is the canonical model selector."
        ),
    ] = None,
    catalog_id: Annotated[
        str | None,
        Doc(
            "The updated canonical model catalog row ID backing this preset. Prefer "
            "this over legacy `model_name` and `model_provider`."
        ),
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
    namespaces: Annotated[
        list[str] | None,
        Doc("The updated namespaces to scope dynamic tool discovery."),
    ] = None,
    tool_approvals: Annotated[
        dict[str, bool] | None,
        Doc(
            "The updated per-tool approval map. True means the tool requires manual approval; false means auto-run."
        ),
    ] = None,
    mcp_integrations: Annotated[
        list[str] | None,
        Doc("The updated workspace MCP integration IDs to expose to the preset."),
    ] = None,
    agents: Annotated[
        dict[str, Any] | None,
        Doc(
            "The updated subagent configuration with `enabled` and `subagents` fields."
        ),
    ] = None,
    retries: Annotated[
        int | None,
        Doc("The updated retry count."),
    ] = None,
    enable_thinking: Annotated[
        bool | None,
        Doc("Whether to enable model thinking where supported."),
    ] = None,
    enable_internet_access: Annotated[
        bool | None,
        Doc("Whether to enable internet access for the preset runtime."),
    ] = None,
    skills: Annotated[
        list[dict[str, Any]] | None,
        Doc("The updated skill bindings for the preset."),
    ] = None,
) -> dict[str, Any]:
    # Build kwargs, only including non-None values
    kwargs: dict[str, Any] = {}
    if name is not None:
        kwargs["name"] = name
    # Deprecated legacy fields retained for backward compatibility.
    # catalog_id is the canonical model selector for new callers.
    if model_name is not None:
        kwargs["model_name"] = model_name
    if model_provider is not None:
        kwargs["model_provider"] = model_provider
    if catalog_id is not None:
        kwargs["catalog_id"] = catalog_id
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
    if namespaces is not None:
        kwargs["namespaces"] = namespaces
    if tool_approvals is not None:
        kwargs["tool_approvals"] = tool_approvals
    if mcp_integrations is not None:
        kwargs["mcp_integrations"] = mcp_integrations
    if agents is not None:
        kwargs["agents"] = agents
    if retries is not None:
        kwargs["retries"] = retries
    if enable_thinking is not None:
        kwargs["enable_thinking"] = enable_thinking
    if enable_internet_access is not None:
        kwargs["enable_internet_access"] = enable_internet_access
    if skills is not None:
        kwargs["skills"] = skills

    return await ctx.agents.aio.update_preset(slug, **kwargs)


@registry.register(
    default_title="Delete agent preset",
    display_group="Agent Presets",
    description="Permanently delete an agent preset from the workspace. The preset is identified by its slug. This action cannot be undone.",
    namespace="ai.agent",
    required_entitlements=["agent_addons"],
)
async def delete_preset(
    slug: Annotated[
        str,
        Doc("The slug identifier of the preset to delete (e.g., 'security-analyst')."),
    ],
) -> None:
    await ctx.agents.aio.delete_preset(slug)
