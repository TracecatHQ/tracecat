from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from typing_extensions import Doc

from tracecat.chat.enums import ChatEntity
from tracecat.runbook.models import (
    RunbookExecuteResponse,
    RunbookRead,
    RunbookExecuteEntity,
    RunbookUpdate,
)
from tracecat.runbook.service import RunbookService
from tracecat_registry import registry


@registry.register(
    namespace="core.runbooks",
    description="List runbooks in the current workspace.",
    default_title="List runbooks",
    display_group="Runbooks",
)
async def list_runbooks(
    limit: Annotated[
        int,
        Doc("Maximum number of runbooks to return (1-100)."),
    ] = 50,
    sort_by: Annotated[
        Literal["created_at", "updated_at"],
        Doc("Field to sort by: 'created_at' or 'updated_at'."),
    ] = "created_at",
    order: Annotated[
        Literal["asc", "desc"],
        Doc("Sort order: 'asc' or 'desc'."),
    ] = "desc",
) -> list[dict[str, Any]]:
    async with RunbookService.with_session() as svc:
        runbooks = await svc.list_runbooks(limit=limit, sort_by=sort_by, order=order)
    return [r.model_dump(mode="json") for r in runbooks]


@registry.register(
    namespace="core.runbooks",
    description="Get a single runbook.",
    default_title="Get runbook",
    display_group="Runbooks",
)
async def get_runbook(
    runbook_id: Annotated[
        str | None,
        Doc("The runbook ID (UUID)."),
    ] = None,
    runbook_alias: Annotated[
        str | None,
        Doc("The runbook alias (can be selected from dropdown)."),
    ] = None,
) -> dict[str, Any]:
    if not runbook_id and not runbook_alias:
        raise ValueError("Either runbook_id or runbook_alias must be provided")
    if runbook_id and runbook_alias:
        raise ValueError("Only one of runbook_id or runbook_alias should be provided")

    async with RunbookService.with_session() as svc:
        runbook = None
        if runbook_id:
            try:
                runbook_uuid = UUID(runbook_id)
            except ValueError:
                raise ValueError(f"Invalid runbook ID format: '{runbook_id}'")
            runbook = await svc.get_runbook(runbook_uuid)
        elif runbook_alias:
            runbook = await svc.get_runbook_by_alias(runbook_alias)

    if not runbook:
        identifier = runbook_id if runbook_id else runbook_alias
        raise ValueError(
            f"Runbook with {'ID' if runbook_id else 'alias'} '{identifier}' not found"
        )
    return RunbookRead.model_validate(runbook, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    namespace="core.runbooks",
    description="Execute a runbook on one or more cases.",
    default_title="Execute runbook",
    display_group="Runbooks",
)
async def execute(
    *,
    runbook_id: Annotated[
        str | None,
        Doc("The runbook ID (UUID) to execute."),
    ] = None,
    runbook_alias: Annotated[
        str | None,
        Doc("The runbook alias to execute (can be selected from dropdown)."),
    ] = None,
    case_ids: Annotated[
        list[str],
        Doc("List of case IDs (UUID strings) to run the runbook on."),
    ],
) -> list[dict[str, Any]]:
    if not runbook_id and not runbook_alias:
        raise ValueError("Either runbook_id or runbook_alias must be provided")
    if runbook_id and runbook_alias:
        raise ValueError("Only one of runbook_id or runbook_alias should be provided")

    async with RunbookService.with_session() as svc:
        runbook = None
        if runbook_id:
            try:
                runbook_uuid = UUID(runbook_id)
            except ValueError:
                raise ValueError(f"Invalid runbook ID format: '{runbook_id}'")
            runbook = await svc.get_runbook(runbook_uuid)
        elif runbook_alias:
            runbook = await svc.get_runbook_by_alias(runbook_alias)

        if not runbook:
            identifier = runbook_id if runbook_id else runbook_alias
            raise ValueError(
                f"Runbook with {'ID' if runbook_id else 'alias'} '{identifier}' not found"
            )

        entities = [
            RunbookExecuteEntity(entity_id=UUID(case_id), entity_type=ChatEntity.CASE)
            for case_id in case_ids
        ]
        responses = await svc.run_runbook(runbook, entities)

    # Return a list of chat execution descriptors
    return [
        RunbookExecuteResponse(
            stream_urls={str(resp.chat_id): resp.stream_url}
        ).model_dump(mode="json")
        for resp in responses
    ]


@registry.register(
    namespace="core.runbooks",
    description="Update a runbook's title, content, summary, or tools.",
    default_title="Update runbook",
    display_group="Runbooks",
)
async def update_runbook(
    runbook_id: Annotated[
        str,
        Doc("The runbook ID (UUID) to update."),
    ],
    title: Annotated[
        str | None,
        Doc("New title for the runbook."),
    ] = None,
    content: Annotated[
        str | None,
        Doc("New content for the runbook."),
    ] = None,
    summary: Annotated[
        str | None,
        Doc("New summary for the runbook."),
    ] = None,
    tools: Annotated[
        list[str] | None,
        Doc("New list of tools for the runbook."),
    ] = None,
) -> dict[str, Any]:
    async with RunbookService.with_session() as svc:
        runbook = await svc.get_runbook(UUID(runbook_id))
        if not runbook:
            raise ValueError(f"Runbook with ID {runbook_id} not found")

        # Build update params
        kwargs: dict[str, Any] = {}
        if title is not None:
            kwargs["title"] = title
        if content is not None:
            kwargs["content"] = content
        if summary is not None:
            kwargs["summary"] = summary
        if tools is not None:
            kwargs["tools"] = tools
        update_params = RunbookUpdate(**kwargs)

        updated_runbook = await svc.update_runbook(runbook, update_params)

    return RunbookRead.model_validate(updated_runbook, from_attributes=True).model_dump(
        mode="json"
    )
