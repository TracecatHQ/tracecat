from typing import Annotated, Any, Literal
from uuid import UUID

from typing_extensions import Doc
from tracecat import config
from tracecat.clients import AuthenticatedServiceClient

from tracecat.chat.enums import ChatEntity
from tracecat.contexts import ctx_role
from tracecat.runbook.models import (
    RunbookExecuteResponse,
    RunbookRead,
    RunbookExecuteEntity,
    RunbookUpdate,
)
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat.runbook.service import RunbookService
from tracecat_registry import registry

from tracecat.types.auth import Role


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
    # Normalize to API shape using RunbookRead
    return [
        RunbookRead.model_validate(r, from_attributes=True).model_dump(mode="json")
        for r in runbooks
    ]


@registry.register(
    namespace="core.runbooks",
    description="Get a single runbook by ID or alias.",
    default_title="Get runbook",
    display_group="Runbooks",
)
async def get_runbook(
    runbook_id_or_alias: Annotated[
        str,
        Doc("The runbook ID (UUID) or alias."),
    ],
) -> dict[str, Any]:
    async with RunbookService.with_session() as svc:
        runbook = await svc.get_runbook(runbook_id_or_alias)
    return RunbookRead.model_validate(runbook, from_attributes=True).model_dump(
        mode="json"
    )


class ApiHTTPClient(AuthenticatedServiceClient):
    """Async httpx client for the executor service."""

    def __init__(self, role: Role | None = None, *args: Any, **kwargs: Any) -> None:
        self._api_base_url = config.TRACECAT__API_URL
        super().__init__(role, *args, base_url=self._api_base_url, **kwargs)
        self.params = self.params.add(
            "workspace_id", str(self.role.workspace_id) if self.role else None
        )
        self.role = self.role or ctx_role.get()


@registry.register(
    namespace="core.runbooks",
    description="Execute a runbook on one or more cases.",
    default_title="Execute runbook",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
    display_group="Runbooks",
)
async def execute(
    runbook_id_or_alias: Annotated[
        str,
        Doc("The runbook ID (UUID) or alias to execute."),
    ],
    case_id: Annotated[
        str,
        Doc("List of case IDs (UUID strings) to run the runbook on."),
    ],
) -> Any:
    async with RunbookService.with_session() as svc:
        # Try to determine if it's a UUID or alias
        runbook = await svc.get_runbook(runbook_id_or_alias)
        if not runbook:
            raise ValueError(
                f"Runbook with ID or alias {runbook_id_or_alias} not found"
            )

    entity = RunbookExecuteEntity(entity_id=UUID(case_id), entity_type=ChatEntity.CASE)
    async with ApiHTTPClient() as client:
        response = await client.post(
            f"/runbooks/{runbook.id}/execute",
            json={"entities": [entity.model_dump(mode="json")]},
        )
        response.raise_for_status()
        raw_results = response.json()

    results = RunbookExecuteResponse.model_validate(raw_results)
    return results.model_dump(mode="json")


@registry.register(
    namespace="core.runbooks",
    description="Update a runbook's title, content, summary, alias, or tools.",
    default_title="Update runbook",
    display_group="Runbooks",
)
async def update_runbook(
    runbook_id_or_alias: Annotated[
        str,
        Doc("The runbook ID (UUID) or alias to update."),
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
    alias: Annotated[
        str | None,
        Doc("New alias for the runbook (must be unique within workspace)."),
    ] = None,
    tools: Annotated[
        list[str] | None,
        Doc("New list of tools for the runbook."),
    ] = None,
) -> dict[str, Any]:
    async with RunbookService.with_session() as svc:
        # Try to determine if it's a UUID or alias
        runbook = await svc.get_runbook(runbook_id_or_alias)
        if not runbook:
            raise ValueError(
                f"Runbook with ID or alias {runbook_id_or_alias} not found"
            )
        # Build update params
        kwargs: dict[str, Any] = {}
        if title is not None:
            kwargs["title"] = title
        if content is not None:
            kwargs["content"] = content
        if summary is not None:
            kwargs["summary"] = summary
        if alias is not None:
            kwargs["alias"] = alias
        if tools is not None:
            kwargs["tools"] = tools
        update_params = RunbookUpdate(**kwargs)

        updated_runbook = await svc.update_runbook(runbook, update_params)

    return RunbookRead.model_validate(updated_runbook, from_attributes=True).model_dump(
        mode="json"
    )
