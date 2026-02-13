from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.cases.tags.schemas import CaseTagRead, InternalCaseTagCreate
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError

router = APIRouter(
    prefix="/internal/cases", tags=["internal-cases"], include_in_schema=False
)


@router.get("/{case_id}/tags", response_model=list[CaseTagRead])
@require_scope("case:read")
async def list_tags(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: UUID4,
) -> list[CaseTagRead]:
    service = CaseTagsService(session, role=role)
    tags = await service.list_tags_for_case(case_id)
    return [
        CaseTagRead(id=tag.id, name=tag.name, ref=tag.ref, color=tag.color)
        for tag in tags
    ]


@router.post(
    "/{case_id}/tags", status_code=status.HTTP_201_CREATED, response_model=CaseTagRead
)
@require_scope("case:create")
async def add_tag(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: UUID4,
    params: InternalCaseTagCreate,
) -> CaseTagRead:
    service = CaseTagsService(session, role=role)
    try:
        tag = await service.add_case_tag(
            case_id, str(params.tag_id), create_if_missing=params.create_if_missing
        )
        return CaseTagRead(id=tag.id, name=tag.name, ref=tag.ref, color=tag.color)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        ) from err
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Tag operation failed"
        ) from e


@router.delete(
    "/{case_id}/tags/{tag_identifier}", status_code=status.HTTP_204_NO_CONTENT
)
@require_scope("case:delete")
async def remove_tag(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: UUID4,
    tag_identifier: str,
) -> None:
    service = CaseTagsService(session, role=role)
    try:
        await service.remove_case_tag(case_id, tag_identifier)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(err)
        ) from err
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found on case"
        ) from err
