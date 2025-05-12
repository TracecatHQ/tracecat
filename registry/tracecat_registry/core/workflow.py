from typing import Annotated, Any, Literal

from tracecat.identifiers.workflow import AnyWorkflowID
from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, registry


@registry.register(
    namespace="core.workflow",
    description="Execute a child workflow. The child workflow inherits the parent's execution context.",
    default_title="Execute child workflow",
    display_group="Workflows",
)
async def execute(
    *,
    workflow_id: Annotated[
        AnyWorkflowID | None,
        Doc(
            "ID of the child workflow to execute. Must be provided if workflow_alias is not provided.",
        ),
    ] = None,
    workflow_alias: Annotated[
        str | None,
        Doc(
            "Alias of the child workflow to execute. Must be provided if workflow_id is not provided.",
        ),
    ] = None,
    trigger_inputs: Annotated[
        dict[str, Any] | None,
        Doc("Inputs to pass to the child workflow."),
    ] = None,
    environment: Annotated[
        str | None,
        Doc(
            "Child workflow's target execution environment. "
            "This is used to isolate secrets across different environments."
            "If not provided, the child workflow's default environment is used. "
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        Doc(
            "Maximum number of seconds to wait for the child workflow to complete. "
            "If not provided, the child workflow's default timeout is used. "
        ),
    ] = None,
    version: Annotated[
        int | None,
        Doc("Version of the child workflow definition, if any."),
    ] = None,
    loop_strategy: Annotated[
        Literal["parallel", "batch", "sequential"],
        Doc("Execution strategy to use for the child workflow."),
    ] = "batch",
    batch_size: Annotated[
        int,
        Doc("Number of child workflows to execute in parallel."),
    ] = 32,
    fail_strategy: Annotated[
        Literal["isolated", "all"],
        Doc("Fail strategy to use when a child workflow fails."),
    ] = "isolated",
    wait_strategy: Annotated[
        Literal["wait", "detach"],
        Doc(
            "Wait strategy to use when waiting for child workflows to complete. "
            "In `wait` mode, this action will wait for all child workflows to complete before returning. "
            "Any child workflow failures will be reported as an error. "
            "In `detach` mode, this action will return immediately after the child workflows are created. "
            "A failing child workflow will not affect the parent. "
        ),
    ] = "wait",
) -> Any:
    raise ActionIsInterfaceError()
