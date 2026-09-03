import uuid
from typing import TypedDict

from fastapi import HTTPException
from starlette.status import HTTP_400_BAD_REQUEST

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
