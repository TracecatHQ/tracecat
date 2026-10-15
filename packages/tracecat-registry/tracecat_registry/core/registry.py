"""Registry action catalog tools for agents."""

from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import ctx, registry


@registry.register(
    namespace="core.registry",
    description=(
        "Search or browse available action definitions by name and description. "
        "Follow `next_cursor` while `has_more` is true, then call "
        "`core.workflow.get_authoring_context` with selected `action_names` "
        "for schemas and credentials. Search is a literal substring."
    ),
    default_title="Search action catalog",
    display_group="Action catalog",
)
async def search_actions(
    *,
    query: Annotated[
        str | None,
        Doc("Literal action name or description substring to search."),
    ] = None,
    namespace: Annotated[
        str | None,
        Doc("Optional namespace prefix, such as `tools` or `tools.example`."),
    ] = None,
    limit: Annotated[
        int,
        Doc("Maximum results per page, from 1 to 200."),
    ] = 50,
    cursor: Annotated[
        str | None,
        Doc("Pass `next_cursor` with the same filters to read the next page."),
    ] = None,
) -> dict[str, Any]:
    """Return compact registry action search results with continuation cursors."""
    return await ctx.workflows.aio.search_actions(
        query=query, namespace=namespace, limit=limit, cursor=cursor
    )
