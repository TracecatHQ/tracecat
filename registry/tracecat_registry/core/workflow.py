from typing import Annotated, Any, Literal

from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, registry


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
        dict[str, Any] | None,
        Doc("Inputs to pass to the subflow."),
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
    ] = "wait",
) -> Any:
    raise ActionIsInterfaceError()
