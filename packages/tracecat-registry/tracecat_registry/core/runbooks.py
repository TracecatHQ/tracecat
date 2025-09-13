from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from typing_extensions import Doc

from tracecat.chat.enums import ChatEntity
from tracecat.prompt.models import PromptRead, PromptRunEntity
from tracecat.prompt.service import PromptService
from tracecat_registry import registry


@registry.register(
    namespace="core.runbooks",
    description="List runbooks (prompts) in the current workspace.",
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
    async with PromptService.with_session() as svc:
        prompts = await svc.list_prompts(limit=limit, sort_by=sort_by, order=order)
    # Normalize to API shape using PromptRead
    return [
        PromptRead.model_validate(p, from_attributes=True).model_dump(mode="json")
        for p in prompts
    ]


@registry.register(
    namespace="core.runbooks",
    description="Get a single runbook (prompt) by ID.",
    default_title="Get runbook",
    display_group="Runbooks",
)
async def get_runbook(
    runbook_id: Annotated[
        str,
        Doc("The runbook ID (UUID)."),
    ],
) -> dict[str, Any]:
    async with PromptService.with_session() as svc:
        prompt = await svc.get_prompt(UUID(runbook_id))
    if not prompt:
        raise ValueError(f"Runbook with ID {runbook_id} not found")
    return PromptRead.model_validate(prompt, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    namespace="core.runbooks",
    description="Execute a runbook on one or more cases.",
    default_title="Execute runbook",
    display_group="Runbooks",
)
async def execute(
    runbook_id: Annotated[
        str,
        Doc("The runbook ID (UUID) to execute."),
    ],
    case_ids: Annotated[
        list[str],
        Doc("List of case IDs (UUID strings) to run the runbook on."),
    ],
) -> list[dict[str, Any]]:
    async with PromptService.with_session() as svc:
        prompt = await svc.get_prompt(UUID(runbook_id))
        if not prompt:
            raise ValueError(f"Runbook with ID {runbook_id} not found")

        entities = [
            PromptRunEntity(entity_id=UUID(case_id), entity_type=ChatEntity.CASE)
            for case_id in case_ids
        ]
        responses = await svc.run_prompt(prompt, entities)

    # Return a list of chat execution descriptors
    return [
        {"chat_id": str(resp.chat_id), "stream_url": resp.stream_url}
        for resp in responses
    ]
