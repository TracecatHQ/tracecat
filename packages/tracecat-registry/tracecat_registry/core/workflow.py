"""Workflow execution UDFs.

NOTE: When `core.workflow.execute` is called within a DSLWorkflow, it is
intercepted and handled as a child workflow execution (subflow) via Temporal's
child workflow machinery. The UDF implementation here is only invoked for
direct calls (e.g., from AI agents or scripts).
"""

from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, registry
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
        ctx = get_context()
    except RuntimeError:
        # No context available - this is likely being called from DSLWorkflow
        # which should have intercepted this action before reaching the UDF.
        # If we got here without context, raise the interface error.
        raise ActionIsInterfaceError()

    # We have context - this is a direct invocation from AI agent or script
    # Note: version, loop_strategy, batch_size, fail_strategy are DSLWorkflow-only params

    try:
        result = await ctx.workflows.execute(
            workflow_alias=workflow_alias,
            trigger_inputs=trigger_inputs,
            environment=environment,
            timeout=timeout,
            wait_strategy=wait_strategy,
            # Pass parent execution ID for correlation (stored in Temporal memo)
            parent_workflow_execution_id=ctx.wf_exec_id,
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
    ctx = get_context()
    return await ctx.workflows.get_status(wf_exec_id)
