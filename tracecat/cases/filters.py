import uuid
from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_400_BAD_REQUEST

from tracecat.auth.types import Role
from tracecat.cases.tags.search import resolve_case_tag_filters
from tracecat.exceptions import TracecatValidationError

UNASSIGNED_ASSIGNEE_IDENTIFIERS = frozenset({"unassigned", "__UNASSIGNED__"})


class ParsedAssigneeFilter(TypedDict):
    assignee_ids: list[uuid.UUID] | None
    include_unassigned: bool


def parse_assignee_filter(assignee_id: list[str] | None) -> ParsedAssigneeFilter:
    parsed_assignee_ids: list[uuid.UUID] = []
    include_unassigned = False
    if assignee_id:
        for identifier in assignee_id:
            if identifier in UNASSIGNED_ASSIGNEE_IDENTIFIERS:
                include_unassigned = True
                continue
            try:
                parsed_assignee_ids.append(uuid.UUID(identifier))
            except ValueError as e:
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail=f"Invalid assignee_id: {identifier}",
                ) from e

    return {
        "assignee_ids": parsed_assignee_ids or None,
        "include_unassigned": include_unassigned,
    }


async def resolve_tag_ids(
    *,
    tags: list[str] | None,
    role: Role,
    session: AsyncSession,
) -> list[uuid.UUID] | None:
    """Resolve each nonempty tag filter in the current workspace or reject it."""
    identifiers = [identifier for identifier in tags or [] if identifier.strip()]
    if not identifiers:
        return None

    if role.workspace_id is None:
        raise ValueError("Workspace ID is required")
    try:
        resolved = await resolve_case_tag_filters(
            session=session, workspace_id=role.workspace_id, tags=identifiers
        )
    except TracecatValidationError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [tag.id for tag in resolved]
