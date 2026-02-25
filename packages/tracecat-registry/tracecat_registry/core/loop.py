from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, registry


@registry.register(
    default_title="Loop start",
    description="Open a do-while control-flow region.",
    display_group="Data Transform",
    namespace="core.loop",
)
def start() -> Any:
    raise ActionIsInterfaceError()


@registry.register(
    default_title="Loop end",
    description=(
        "Close a do-while control-flow region and evaluate whether execution should "
        "loop back to the matching `core.loop.start`."
    ),
    display_group="Data Transform",
    namespace="core.loop",
)
def end(
    condition: Annotated[
        str,
        Doc(
            "Expression evaluated after the loop body; truthy values continue looping."
        ),
    ],
    max_iterations: Annotated[
        int,
        Doc("Maximum number of loop iterations before failing."),
    ] = 100,
) -> Any:
    raise ActionIsInterfaceError()
