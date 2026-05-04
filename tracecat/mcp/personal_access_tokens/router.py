from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.dependencies import OrgUserOnlyRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.mcp.personal_access_tokens.schemas import (
    IssuedMCPPersonalAccessToken,
    MCPPersonalAccessTokenCreate,
    MCPPersonalAccessTokenIssueResponse,
    MCPPersonalAccessTokenRead,
)
from tracecat.mcp.personal_access_tokens.service import MCPPersonalAccessTokenService
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(
    prefix="/organization/mcp-personal-access-tokens",
    tags=["mcp_personal_access_tokens"],
)


@router.get("", response_model=CursorPaginatedResponse[MCPPersonalAccessTokenRead])
@require_scope("org:read")
async def list_mcp_personal_access_tokens(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[MCPPersonalAccessTokenRead]:
    service = MCPPersonalAccessTokenService(session, role=role)
    try:
        page = await service.list_tokens(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except TracecatAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    return CursorPaginatedResponse(
        items=[
            MCPPersonalAccessTokenRead.model_validate(item, from_attributes=True)
            for item in page.items
        ],
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@router.post(
    "",
    response_model=MCPPersonalAccessTokenIssueResponse,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("org:read")
async def create_mcp_personal_access_token(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    params: MCPPersonalAccessTokenCreate,
) -> MCPPersonalAccessTokenIssueResponse:
    service = MCPPersonalAccessTokenService(session, role=role)
    try:
        token, raw_token = await service.create_token(
            name=params.name,
            workspace_id=params.workspace_id,
            expires_at=params.expires_at,
        )
    except TracecatAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    return MCPPersonalAccessTokenIssueResponse(
        issued_token=IssuedMCPPersonalAccessToken(
            raw_token=raw_token,
            token=MCPPersonalAccessTokenRead.model_validate(
                token,
                from_attributes=True,
            ),
        )
    )


@router.post("/{token_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:read")
async def revoke_mcp_personal_access_token(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    token_id: UUID,
) -> None:
    service = MCPPersonalAccessTokenService(session, role=role)
    try:
        await service.revoke_token(token_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except TracecatAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
