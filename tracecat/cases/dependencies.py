import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from pydantic import Field, TypeAdapter, ValidationError
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_CONTENT,
)

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.cases.constants import MAX_PAYLOAD_PATH_KEYS, MAX_PAYLOAD_PATHS
from tracecat.cases.search_adapters import CurrentUserRequiredError
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

_PAYLOAD_PATHS_ADAPTER = TypeAdapter(
    Annotated[
        list[
            Annotated[list[str], Field(min_length=1, max_length=MAX_PAYLOAD_PATH_KEYS)]
        ],
        Field(max_length=MAX_PAYLOAD_PATHS),
    ]
)


def payload_paths_query(
    payload_paths: Annotated[
        str | None,
        Query(
            description=(
                'JSON-encoded array of up to 100 object paths, e.g. [["alert","source","ip"]]. '
                "Each path contains 1 to 32 literal keys. Preserves nesting, combines "
                "with payload_fields, and includes the subset automatically. Selected "
                "parents include their whole value. Missing paths are omitted; nulls "
                "are preserved. Arrays can be selected whole, without index traversal. "
                "An empty list acts as omitted."
            )
        ),
    ] = None,
) -> list[list[str]] | None:
    """Decode nested paths from the string form supported by query parameters."""
    if payload_paths is None:
        return None
    try:
        return _PAYLOAD_PATHS_ADAPTER.validate_json(payload_paths)
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {**error, "loc": ("query", "payload_paths", *error["loc"])}
                for error in exc.errors()
            ]
        ) from exc


PayloadPathsQuery = Annotated[list[list[str]] | None, Depends(payload_paths_query)]


async def resolve_case_refs(
    service: CasesService, case_refs: list[str]
) -> list[uuid.UUID]:
    """Resolve case UUIDs and short IDs, mapping bad refs to 422 and unknown ones to 404."""
    try:
        return await service.resolve_case_ids(case_refs)
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc


async def executor_case_id_path_dependency(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: Annotated[str, Path()],
) -> uuid.UUID:
    (resolved,) = await resolve_case_refs(CasesService(session, role), [case_id])
    return resolved


ExecutorCaseIDPath = Annotated[uuid.UUID, Depends(executor_case_id_path_dependency)]
"""A case ID path param that accepts a UUID or a short ID (``CASE-0042`` or ``42``)."""


def case_query_http_error(
    exc: ValueError | TracecatValidationError | TracecatNotFoundError,
) -> HTTPException:
    """Map a rejected case query to an HTTP error.

    A missing or hidden saved view is a 404. Other errors are 400s; typed ones
    keep a machine-readable ``code`` so clients need not parse the message.
    """
    if isinstance(exc, TracecatNotFoundError):
        return HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, CurrentUserRequiredError):
        return HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc))
