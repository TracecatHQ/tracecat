"""HTTP routes for the platform skill library."""

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.agent.skill.library.schemas import LibrarySkillRead
from tracecat.agent.skill.library.service import (
    SkillLibraryErrorCode,
    SkillLibraryService,
)
from tracecat.agent.skill.schemas import SkillRead
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(prefix="/skill-library", tags=["skill-library"])


def _validation_http_exception(err: TracecatValidationError) -> HTTPException:
    detail = err.detail if isinstance(err.detail, dict) else {}
    status_code = (
        status.HTTP_409_CONFLICT
        if detail.get("code") == SkillLibraryErrorCode.IN_USE.value
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(
        status_code=status_code, detail={**detail, "message": str(err)}
    )


@router.get(
    "",
    response_model=CursorPaginatedResponse[LibrarySkillRead],
    responses={status.HTTP_400_BAD_REQUEST: {"description": "Invalid cursor"}},
)
@require_scope("agent:read")
async def list_library_skills(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[LibrarySkillRead]:
    """List library skills with this workspace's install state."""
    service = SkillLibraryService(session, role=role)
    try:
        return await service.list_skills(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post(
    "/{slug}/install",
    response_model=LibrarySkillRead,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown library skill"}},
)
@require_scope("agent:create")
async def install_library_skill(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    slug: str,
) -> LibrarySkillRead:
    """Install a library skill into this workspace."""
    service = SkillLibraryService(session, role=role)
    try:
        return await service.install(slug)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete(
    "/{slug}/install",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Library skill is not installed"},
        status.HTTP_409_CONFLICT: {"description": "A preset head binds the skill"},
    },
)
@require_scope("agent:delete")
async def uninstall_library_skill(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    slug: str,
) -> None:
    """Uninstall a library skill that no preset binds."""
    service = SkillLibraryService(session, role=role)
    try:
        await service.uninstall(slug)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise _validation_http_exception(e) from e


@router.post(
    "/{slug}/fork",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Forked skill is invalid"},
        status.HTTP_404_NOT_FOUND: {"description": "Unknown library skill"},
    },
)
@require_scope("agent:create")
async def fork_library_skill(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    slug: str,
) -> SkillRead:
    """Copy a library skill into an editable workspace skill."""
    service = SkillLibraryService(session, role=role)
    try:
        return await service.fork(slug)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise _validation_http_exception(e) from e
