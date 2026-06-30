"""Workflow execution UDFs.

NOTE: When `core.workflow.execute` is called within a DSLWorkflow, it is
intercepted and handled as a child workflow execution (subflow) via Temporal's
child workflow machinery. The UDF implementation here is only invoked for
direct calls (e.g., from AI agents or scripts).
"""

from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, ctx, registry
from tracecat_registry.context import get_context
from tracecat_registry.sdk.workflows import (
    WorkflowExecutionError,
    WorkflowExecutionTimeout,
)


@registry.register(
    namespace="core.workflow",
    description="Execute a subflow.",
    default_title="Execute subflow",
    display_group="Workflows",
)
async def execute(
    *,
    workflow_alias: Annotated[
        str | None,
        Doc(
            "Alias of the subflow to execute. Must be provided.",
        ),
    ],
    trigger_inputs: Annotated[
        Any | None,
        Doc("Inputs to pass to the subflow (arbitrary JSON)."),
    ] = None,
    environment: Annotated[
        str | None,
        Doc(
            "Subflow's target execution environment. "
            "This is used to isolate secrets across different environments."
            "If not provided, the subflow's default environment is used. "
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        Doc(
            "Maximum number of seconds to wait for the subflow to complete. "
            "If not provided, the subflow's default timeout is used. "
        ),
    ] = None,
    version: Annotated[
        int | None,
        Doc("Version of the subflow definition, if any."),
    ] = None,
    loop_strategy: Annotated[
        Literal["parallel", "batch", "sequential"],
        Doc("Execution strategy to use for the subflow."),
    ] = "batch",
    batch_size: Annotated[
        int,
        Doc("Number of subflows to execute in parallel."),
    ] = 32,
    fail_strategy: Annotated[
        Literal["isolated", "all"],
        Doc("Fail strategy to use when a subflow fails."),
    ] = "isolated",
    wait_strategy: Annotated[
        Literal["wait", "detach"],
        Doc(
            "Wait strategy to use when waiting for subflows to complete. "
            "In `wait` mode, this action will wait for all subflows to complete before returning. "
            "Any subflow failures will be reported as an error. "
            "In `detach` mode, this action will return immediately after the subflows are created. "
            "A failing subflow will not affect the parent. "
        ),
    ] = "detach",
) -> Any:
    """Execute a workflow by alias.

    When called within a DSLWorkflow (normal workflow execution), this action
    is intercepted and handled as a child workflow execution via Temporal.
    The UDF implementation here is only invoked for direct calls from AI agents.

    Note: version, loop_strategy, batch_size, and fail_strategy are only used
    when executed within DSLWorkflow. For direct invocation, only workflow_alias,
    trigger_inputs, environment, timeout, and wait_strategy are used.

    Returns:
        For wait_strategy="detach": {"wf_id": str, "wf_exec_id": str, "status": "STARTED"}
        For wait_strategy="wait": The workflow result if successful.

    Raises:
        ActionIsInterfaceError: If called from within a DSLWorkflow context
            (the DSLWorkflow handles this action via child workflow machinery).
        WorkflowExecutionError: If the workflow fails, is canceled, or terminated.
        WorkflowExecutionTimeout: If timeout is reached while waiting.
        ActionIsInterfaceError: If no context is available (handled by DSLWorkflow).
    """
    # Try to get the registry context for direct invocation
    try:
        context = get_context()
    except RuntimeError:
        # No context available - this is likely being called from DSLWorkflow
        # which should have intercepted this action before reaching the UDF.
        # If we got here without context, raise the interface error.
        raise ActionIsInterfaceError()

    # We have context - this is a direct invocation from AI agent or script
    # Note: version, loop_strategy, batch_size, fail_strategy are DSLWorkflow-only params

    try:
        result = await context.workflows.execute(
            workflow_alias=workflow_alias,
            trigger_inputs=trigger_inputs,
            environment=environment,
            timeout=timeout,
            wait_strategy=wait_strategy,
            # Pass parent execution ID for correlation (stored in Temporal memo)
            parent_workflow_execution_id=context.wf_exec_id,
        )
        return result
    except WorkflowExecutionError:
        # Re-raise workflow execution errors (failed, canceled, terminated)
        raise
    except WorkflowExecutionTimeout:
        # Re-raise timeout errors
        raise


@registry.register(
    namespace="core.workflow",
    description=(
        "Create a new workflow, optionally pre-filled from `definition_yaml`. "
        "Read the `tracecat-manage-workflows` skill first."
    ),
    default_title="Create workflow",
    display_group="Workflows",
)
async def create_workflow(
    *,
    title: Annotated[
        str | None,
        Doc(
            "Title for the new workflow (3-100 characters). For an empty create "
            "a timestamped title is used when omitted; with `definition_yaml` "
            "the title must come from here or a `title:` in the YAML."
        ),
    ] = None,
    description: Annotated[
        str | None,
        Doc("Optional description for the new workflow (up to 1000 characters)."),
    ] = None,
    definition_yaml: Annotated[
        str | None,
        Doc(
            "Optional full workflow definition as YAML (actions, layout, case "
            "trigger). When provided, the workflow is created with these actions "
            "instead of being empty. The complete workflow belongs under a "
            "top-level `definition:` key. Schedules are not created here — add "
            "them afterwards with `edit_workflow`."
        ),
    ] = None,
) -> dict[str, Any]:
    """Create a workflow and return ``{"id", "title"}``."""
    return await ctx.workflows.aio.create_workflow(
        title=title, description=description, definition_yaml=definition_yaml
    )


@registry.register(
    namespace="core.workflow",
    description=(
        "Read a workflow's editable draft (`draft_revision` + `draft_document`). "
        "Call before `edit_workflow`. See the `tracecat-manage-workflows` skill."
    ),
    default_title="Get workflow",
    display_group="Workflows",
)
async def get_workflow(
    *,
    workflow_id: Annotated[
        str,
        Doc("The workflow ID to read (short `wf_...` or full format)."),
    ],
) -> dict[str, Any]:
    """Read a workflow's editable draft document and revision."""
    return await ctx.workflows.aio.get_workflow(workflow_id=workflow_id)


@registry.register(
    namespace="core.workflow",
    description=(
        "Edit a workflow's draft with RFC 6902 JSON Patch ops (get_workflow → "
        "patch → edit_workflow). Read the `tracecat-manage-workflows` skill first."
    ),
    default_title="Edit workflow",
    display_group="Workflows",
)
async def edit_workflow(
    *,
    workflow_id: Annotated[
        str,
        Doc("The workflow ID to edit (short `wf_...` or full format)."),
    ],
    base_revision: Annotated[
        str,
        Doc(
            "The `draft_revision` returned by `get_workflow`. The edit is "
            "rejected with a conflict if the draft changed since then."
        ),
    ],
    patch_ops: Annotated[
        list[dict[str, Any]],
        Doc(
            "RFC 6902 JSON Patch operations to apply to the draft document. Each "
            'op is an object like {"op": "add", "path": "/definition/actions/-", '
            '"value": {...}}. Supported ops: add, remove, replace, move, copy, '
            "test."
        ),
    ],
    validate_only: Annotated[
        bool,
        Doc("When true, validate the patch without persisting changes."),
    ] = False,
) -> dict[str, Any]:
    """Apply JSON Patch edits to a workflow draft and return the new revision."""
    return await ctx.workflows.aio.edit_workflow(
        workflow_id=workflow_id,
        base_revision=base_revision,
        patch_ops=patch_ops,
        validate_only=validate_only,
    )


@registry.register(
    namespace="core.workflow",
    description=(
        "Get action schemas, required secrets, and example args before writing "
        "an action's `args:`. Resolve by `action_names` or `query`. See the "
        "`tracecat-manage-workflows` skill."
    ),
    default_title="Get workflow authoring context",
    display_group="Workflows",
)
async def get_authoring_context(
    *,
    action_names: Annotated[
        list[str] | None,
        Doc(
            "Fully qualified action names to fetch context for (e.g. "
            "`['core.http_request', 'ai.agent']`). Takes precedence over `query`."
        ),
    ] = None,
    query: Annotated[
        str | None,
        Doc(
            "Search string to resolve actions by name/description when "
            "`action_names` is not provided."
        ),
    ] = None,
) -> dict[str, Any]:
    """Return action schemas, secret/variable hints, and example args."""
    return await ctx.workflows.aio.get_authoring_context(
        action_names=action_names,
        query=query,
    )


@registry.register(
    namespace="core.workflow",
    description="Get the status of a workflow execution.",
    default_title="Get workflow status",
    display_group="Workflows",
)
async def get_status(
    *,
    wf_exec_id: Annotated[
        str,
        Doc("The workflow execution ID to check."),
    ],
) -> dict[str, Any]:
    """Get the status of a workflow execution.

    Returns:
        dict containing:
            - wf_exec_id: Execution ID
            - status: RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT
            - start_time: When execution started (ISO format or None)
            - close_time: When execution completed (ISO format or None)
            - result: Workflow result (if completed successfully)

    Raises:
        RuntimeError: If no context is available.
    """
    return await ctx.workflows.aio.get_status(wf_exec_id)
