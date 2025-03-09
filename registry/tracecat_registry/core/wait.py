from tracecat_registry import ActionIsInterfaceError, registry


from typing import Annotated, Any
from typing_extensions import Doc


@registry.register(
    namespace="core.wait",
    description="Wait for a response from an external communication channel.",
    default_title="Wait For Response",
    display_group="Wait",
)
async def response(
    *,
    ref: Annotated[
        str,
        Doc("The reference of the signal to wait for."),
    ],
    channel: Annotated[
        str | None,
        Doc("The communication channel to wait for the response on."),
    ] = None,
    timeout: Annotated[
        float | None,
        Doc("The timeout for the signal."),
    ] = None,
) -> Any:
    raise ActionIsInterfaceError()
