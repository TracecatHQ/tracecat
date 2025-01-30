from typing import Annotated, Any, Literal

from tracecat.identifiers.workflow import AnyWorkflowID
from typing_extensions import Doc

from tracecat_registry import RegistryActionError, registry


@registry.register(
    namespace="core.workflow",
    description="Execute a child workflow. The child workflow inherits the parent's execution context.",
    default_title="Execute Child Workflow",
    display_group="Workflows",
)
async def execute(
    *,
    workflow_id: Annotated[
        AnyWorkflowID | None,
        Doc(
            "The ID of the child workflow to execute. Must be provided if workflow_alias is not provided.",
        ),
    ] = None,
    workflow_alias: Annotated[
        str | None,
        Doc(
            "The alias of the child workflow to execute. Must be provided if workflow_id is not provided.",
        ),
    ] = None,
    trigger_inputs: Annotated[
        dict[str, Any] | None,
        Doc("The inputs to pass to the child workflow."),
    ] = None,
    environment: Annotated[
        str | None,
        Doc(
            "The child workflow's target execution environment. "
            "This is used to isolate secrets across different environments."
            "If not provided, the child workflow's default environment is used. "
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        Doc(
            "The maximum number of seconds to wait for the child workflow to complete. "
            "If not provided, the child workflow's default timeout is used. "
        ),
    ] = None,
    version: Annotated[
        int | None,
        Doc("The version of the child workflow definition, if any."),
    ] = None,
    loop_strategy: Annotated[
        Literal["parallel", "batch", "sequential"],
        Doc("The execution strategy to use for the child workflow."),
    ] = "parallel",
    batch_size: Annotated[
        int,
        Doc("The number of child workflows to execute in parallel."),
    ] = 16,
    fail_strategy: Annotated[
        Literal["isolated", "all"],
        Doc("Fail strategy to use when a child workflow fails."),
    ] = "isolated",
) -> Any:
    raise RegistryActionError(
        "This UDF only defines a controller interface and cannot be invoked directly."
        "If you are seeing this error, please contact your administrator."
    )
