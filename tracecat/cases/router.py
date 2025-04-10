import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import DBAPIError

from tracecat.auth.credentials import RoleACL
from tracecat.auth.models import UserRead
from tracecat.cases.models import (
    CaseCommentCreate,
    CaseCommentRead,
    CaseCommentUpdate,
    CaseCreate,
    CaseCustomFieldRead,
    CaseFieldCreate,
    CaseFieldRead,
    CaseFieldUpdate,
    CaseRead,
    CaseReadMinimal,
    CaseUpdate,
)
from tracecat.cases.service import CaseCommentsService, CaseFieldsService, CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.auth import AccessLevel, Role

cases_router = APIRouter(prefix="/cases", tags=["cases"])
case_fields_router = APIRouter(prefix="/case-fields", tags=["cases"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]
WorkspaceAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=AccessLevel.ADMIN,
    ),
]


# Case Management


@cases_router.get("")
async def list_cases(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[CaseReadMinimal]:
    """List all cases."""
    service = CasesService(session, role)
    cases = await service.list_cases()
    return [
        CaseReadMinimal(
            id=case.id,
            created_at=case.created_at,
            updated_at=case.updated_at,
            short_id=f"CASE-{case.case_number:04d}",
            summary=case.summary,
            status=case.status,
            priority=case.priority,
            severity=case.severity,
        )
        for case in cases
    ]


@cases_router.get("/{case_id}")
async def get_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseRead:
    """Get a specific case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    fields = await service.fields.get_fields(case) or {}
    field_definitions = await service.fields.list_fields()
    final_fields = []
    for defn in field_definitions:
        f = CaseFieldRead.from_sa(defn)
        final_fields.append(
            CaseCustomFieldRead(
                id=f.id,
                type=f.type,
                description=f.description,
                nullable=f.nullable,
                default=f.default,
                reserved=f.reserved,
                value=fields.get(f.id),
            )
        )

    # Match up the fields with the case field definitions
    return CaseRead(
        id=case.id,
        short_id=f"CASE-{case.case_number:04d}",
        created_at=case.created_at,
        updated_at=case.updated_at,
        summary=case.summary,
        status=case.status,
        priority=case.priority,
        severity=case.severity,
        description=case.description,
        fields=final_fields,
    )


@cases_router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseCreate,
) -> None:
    """Create a new case."""
    service = CasesService(session, role)
    await service.create_case(params)


@cases_router.patch("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseUpdate,
    case_id: uuid.UUID,
) -> None:
    """Update a case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    try:
        await service.update_case(case, params)
    except DBAPIError as e:
        while (cause := e.__cause__) is not None:
            e = cause
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@cases_router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> None:
    """Delete a case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    await service.delete_case(case)


# Case Comments
# Support comments as a first class activity type.
# We anticipate having other complex comment functionality in the future.
@cases_router.get("/{case_id}/comments", status_code=status.HTTP_200_OK)
async def list_comments(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseCommentRead]:
    """List all comments for a case."""
    # Get the case first
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    # Execute join query directly in the endpoint
    comments_svc = CaseCommentsService(session, role)
    res = []
    for comment, user in await comments_svc.list_comments(case):
        comment_data = CaseCommentRead.model_validate(comment, from_attributes=True)
        if user:
            comment_data.user = UserRead.model_validate(user, from_attributes=True)
        res.append(comment_data)
    return res


@cases_router.post("/{case_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseCommentCreate,
) -> None:
    """Create a new comment on a case."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    await comments_svc.create_comment(case, params)


@cases_router.patch(
    "/{case_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
    params: CaseCommentUpdate,
) -> None:
    """Update an existing comment."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    await comments_svc.update_comment(comment, params)


@cases_router.delete(
    "/{case_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
) -> None:
    """Delete a comment."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    await comments_svc.delete_comment(comment)


# Case Fields


@case_fields_router.get("")
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[CaseFieldRead]:
    """List all case fields."""
    service = CaseFieldsService(session, role)
    columns = await service.list_fields()
    return [CaseFieldRead.from_sa(column) for column in columns]


@case_fields_router.post("", status_code=status.HTTP_201_CREATED)
async def create_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    params: CaseFieldCreate,
) -> None:
    """Create a new case field."""
    service = CaseFieldsService(session, role)
    await service.create_field(params)


@case_fields_router.patch("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    field_id: str,
    params: CaseFieldUpdate,
) -> None:
    """Update a case field."""
    service = CaseFieldsService(session, role)
    await service.update_field(field_id, params)


@case_fields_router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    field_id: str,
) -> None:
    """Delete a case field."""
    service = CaseFieldsService(session, role)
    await service.delete_field(field_id)
