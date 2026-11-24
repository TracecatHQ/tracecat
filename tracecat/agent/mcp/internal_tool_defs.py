"""Names, parameter schemas and MCP definitions of the internal agent tools.

Kept apart from the handlers in ``internal_tools`` so the modules that only
need tool names or schemas (tool compilation, chat defaults, preset
validation) don't import the handlers' service dependencies, which import the
agent stack in turn.
"""

from __future__ import annotations

import uuid
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from tracecat import config
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.preset.schemas import AgentPresetUpdate
from tracecat.identifiers.workflow import WorkflowExecutionID

# Tool name constants - these are the canonical names for internal tools
BUILDER_INTERNAL_TOOL_NAMES = [
    "internal.builder.get_preset_summary",
    "internal.builder.list_available_tools",
    "internal.builder.update_preset",
    "internal.builder.list_sessions",
    "internal.builder.get_session",
]

# Workflow tools any agent can opt into by listing them in its actions.
# They run in the tool-serving process, so a run never holds an executor slot
# or a turn while it waits on a workflow.
WORKFLOW_INTERNAL_TOOL_SCOPES: dict[str, str] = {
    "internal.workflows.start": "workflow:execute",
    "internal.workflows.get": "workflow:read",
    "internal.workflows.cancel": "workflow:terminate",
}
"""The scope a user needs to be offered each workflow tool, as for the MCP tools."""
WORKFLOW_INTERNAL_TOOL_NAMES = list(WORKFLOW_INTERNAL_TOOL_SCOPES)

# Registry actions that are bundled with the builder assistant
# These are standard registry tools, not internal tools
BUILDER_BUNDLED_ACTIONS = [
    "core.table.download",
    "core.table.get_table_metadata",
    "core.table.list_tables",
    "core.table.search_rows",
]


# -----------------------------------------------------------------------------
# Tool Definitions for MCP
# -----------------------------------------------------------------------------


class ListAvailableToolsParams(BaseModel):
    """Parameters for list_available_tools."""

    query: str = Field(
        ...,
        description="The query to search for actions.",
        min_length=1,
        max_length=100,
    )


class ListSessionsParams(BaseModel):
    """Parameters for list_sessions."""

    limit: int = Field(
        default=config.TRACECAT__LIMIT_AGENT_SESSIONS_DEFAULT,
        description="Maximum number of sessions to return.",
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    )
    created_by: uuid.UUID | None = Field(
        default=None,
        description="Filter by session creator. Omit to list the entire workspace.",
    )


class GetSessionParams(BaseModel):
    """Parameters for get_session."""

    session_id: str = Field(
        ...,
        description="The UUID of the session to retrieve.",
    )


class StartWorkflowParams(BaseModel):
    """Parameters for internal.workflows.start."""

    workflow_id: str | None = Field(
        default=None,
        description="Workflow ID (short `wf_...` or UUID). Give this or `workflow_alias`.",
    )
    workflow_alias: str | None = Field(
        default=None,
        description="Workflow alias. Give this or `workflow_id`.",
    )
    inputs: dict[str, Any] | None = Field(
        default=None,
        description="Trigger inputs for the run, matching the workflow's input schema.",
    )
    use_draft: bool = Field(
        default=False,
        description=(
            "Run the current draft instead of a published version. "
            "Defaults to false (published)."
        ),
    )
    version: int | None = Field(
        default=None,
        description=(
            "Published version to run. Null runs the current published version. "
            "Ignored when `use_draft` is true."
        ),
    )

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.workflow_id is None) == (self.workflow_alias is None):
            raise ValueError("Give exactly one of workflow_id or workflow_alias")
        return self


class WorkflowRunParams(BaseModel):
    """Parameters for internal.workflows.get and internal.workflows.cancel."""

    workflow_execution_id: WorkflowExecutionID = Field(
        ...,
        description=(
            "The run's `workflow_execution_id` (`wf_.../exec_...`), "
            "as returned by internal.workflows.start."
        ),
    )


def get_builder_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Return MCPToolDefinition for each builder internal tool.

    These definitions are used by the proxy MCP server to expose the tools
    to the agent runtime with proper JSON schemas.
    """
    return {
        "internal.builder.get_preset_summary": MCPToolDefinition(
            name="internal.builder.get_preset_summary",
            description="Return the latest configuration for this agent preset, including tools and approval rules.",
            parameters_json_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        "internal.builder.list_available_tools": MCPToolDefinition(
            name="internal.builder.list_available_tools",
            description="Return available actions with configuration readiness metadata. Use this to suggest useful tools and only add configured ones.",
            parameters_json_schema=ListAvailableToolsParams.model_json_schema(),
        ),
        "internal.builder.update_preset": MCPToolDefinition(
            name="internal.builder.update_preset",
            description=(
                "Patch selected fields on the agent preset and return the updated record. "
                "Only include fields you want to change. "
                "Supported fields: instructions (system prompt), actions (list of tool identifiers), "
                "namespaces (scope for tool discovery), tool_approvals"
                " (map of tool_name to bool. If true, require human-in-the-loop approval. "
                "If false, auto-run tool without approval). "
                "New actions can only be added when they are configured (required secrets/keys available)."
            ),
            parameters_json_schema=AgentPresetUpdate.model_json_schema(),
        ),
        "internal.builder.list_sessions": MCPToolDefinition(
            name="internal.builder.list_sessions",
            description=(
                "List workspace agent sessions where this preset is being used. "
                "Returns all teammates by default; optionally filter by creator."
            ),
            parameters_json_schema=ListSessionsParams.model_json_schema(),
        ),
        "internal.builder.get_session": MCPToolDefinition(
            name="internal.builder.get_session",
            description="Get the full message history and metadata for a specific agent session.",
            parameters_json_schema=GetSessionParams.model_json_schema(),
        ),
    }


def get_workflow_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Return MCPToolDefinition for each workflow internal tool."""
    return {
        "internal.workflows.start": MCPToolDefinition(
            name="internal.workflows.start",
            description=(
                "Start a workflow run and return its `workflow_execution_id` at once, "
                "without waiting for the run to finish. Check on it later with "
                "internal.workflows.get."
            ),
            parameters_json_schema=StartWorkflowParams.model_json_schema(),
        ),
        "internal.workflows.get": MCPToolDefinition(
            name="internal.workflows.get",
            description=(
                "Get a workflow run's status (RUNNING, COMPLETED, FAILED, CANCELED, "
                "TERMINATED, TIMED_OUT). Includes `result` when COMPLETED and "
                "`error` when FAILED. Returns at once; call again later while RUNNING."
            ),
            parameters_json_schema=WorkflowRunParams.model_json_schema(),
        ),
        "internal.workflows.cancel": MCPToolDefinition(
            name="internal.workflows.cancel",
            description=(
                "Request graceful cancellation of a workflow run. The run may take a "
                "moment to stop; confirm with internal.workflows.get."
            ),
            parameters_json_schema=WorkflowRunParams.model_json_schema(),
        ),
    }


def get_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Return MCPToolDefinition for every internal tool."""
    return {
        **get_builder_internal_tool_definitions(),
        **get_workflow_internal_tool_definitions(),
    }
