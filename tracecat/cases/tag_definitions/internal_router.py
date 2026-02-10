from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.tags.schemas import TagCreate

router = APIRouter(
    prefix="/internal/case-tags", tags=["internal-case-tags"], include_in_schema=False
)


@router.get("", response_model=list[CaseTagRead])
@require_scope("case:read")
async def executor_list_case_tags(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> list[CaseTagRead]:
    service = CaseTagsService(session=session, role=role)
    tags = await service.list_workspace_tags()
    return [CaseTagRead.model_validate(tag, from_attributes=True) for tag in tags]


@router.post("", response_model=CaseTagRead, status_code=status.HTTP_201_CREATED)
@require_scope("case:create")
async def executor_create_case_tag(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: TagCreate,
) -> CaseTagRead:
    service = CaseTagsService(session=session, role=role)
    try:
        tag = await service.create_tag(params)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case tag already exists",
        ) from err
    return CaseTagRead.model_validate(tag, from_attributes=True)
