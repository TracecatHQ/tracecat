from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from tracecat.dsl.common import DSLRunArgs
from tracecat.registry import RegistryUDFError, registry


class ChildWorkflowExecutionOptions(BaseModel):
    loop_strategy: Literal["sequential", "parallel", "batch"] = "parallel"
    batch_size: int = 16
    fail_strategy: Literal["strict", "skip"] = "strict"


@registry.register(
    namespace="core.workflow",
    version="0.1.0",
    description="Execute a child workflow. The child workflow inherits the parent's execution context.",
    default_title="Execute Child Workflow",
    display_group="Workflows",
)
async def execute(
    workflow_title: Annotated[
        str,
        Field(
            ...,
            description=("The title of the child workflow. "),
        ),
    ],
    trigger_inputs: Annotated[
        dict[str, Any],
        Field(
            ...,
            description="The inputs to pass to the child workflow.",
        ),
    ],
    version: Annotated[
        int | None,
        Field(..., description="The version of the child workflow definition, if any."),
    ] = None,
    loop_strategy: Annotated[
        Literal["parallel", "batch", "sequential"],
        Field(
            ...,
            description="The execution strategy to use for the child workflow.",
        ),
    ] = "parallel",
    batch_size: Annotated[
        int,
        Field(
            ...,
            description="The number of child workflows to execute in parallel.",
        ),
    ] = 16,
    fail_strategy: Annotated[
        Literal["isolated", "all"],
        Field(
            ...,
            description="Fail strategy to use when a child workflow fails.",
        ),
    ] = "isolated",
) -> DSLRunArgs:
    raise RegistryUDFError(
        "This UDF only defines a controller interface and cannot be invoked directly."
        "If you are seeing this error, please contact your administrator."
    )
