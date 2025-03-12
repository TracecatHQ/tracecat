from tracecat_registry import ActionIsInterfaceError, registry


from typing import Annotated, Any
from typing_extensions import Doc


@registry.register(
    namespace="core.interact",
    description="Wait for a response from an incoming interaction.",
    default_title="Wait For Response",
    display_group="Interact",
)
async def response(
    *,
    interaction_id: Annotated[
        str,
        Doc("The identifier for the interaction to wait for."),
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
