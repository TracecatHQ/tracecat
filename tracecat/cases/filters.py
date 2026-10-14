import uuid
from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_400_BAD_REQUEST

from tracecat.auth.types import Role
from tracecat.cases.tags.service import CaseTagsService

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

    tags_service = CaseTagsService(session, role)
    tag_ids: list[uuid.UUID] = []
    for identifier in identifiers:
        try:
            tag = await tags_service.get_tag_by_ref_or_id(identifier)
        except NoResultFound as exc:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Unknown case tag filter: {identifier!r}",
            ) from exc
        tag_ids.append(tag.id)
    return tag_ids
