"""Service layer for MCP personal access tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.api_keys import (
    generate_managed_api_key,
    parse_managed_api_key,
    verify_api_key,
)
from tracecat.authz.controls import require_scope
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    MCPPersonalAccessToken,
    Membership,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.mcp.personal_access_tokens.constants import MCP_PAT_PREFIX
from tracecat.mcp.personal_access_tokens.types import MCPPATIdentity
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseOrgService


class IssuedMCPPersonalAccessTokenResult(NamedTuple):
    token: MCPPersonalAccessToken
    raw_token: str


async def _user_can_access_workspace(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> bool:
    stmt = (
        select(Workspace.id)
        .outerjoin(
            Membership,
            sa.and_(
                Membership.workspace_id == Workspace.id,
                Membership.user_id == user_id,
            ),
        )
        .outerjoin(
            OrganizationMembership,
            sa.and_(
                OrganizationMembership.organization_id == Workspace.organization_id,
                OrganizationMembership.user_id == user_id,
            ),
        )
        .where(
            Workspace.id == workspace_id,
            Workspace.organization_id == organization_id,
            sa.or_(
                Membership.user_id.is_not(None),
                OrganizationMembership.user_id.is_not(None),
            ),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


class MCPPersonalAccessTokenService(BaseOrgService):
    service_name = "mcp_personal_access_tokens"

    @require_scope("org:read")
    async def list_tokens(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[MCPPersonalAccessToken]:
        paginator = BaseCursorPaginator(self.session)
        stmt = select(MCPPersonalAccessToken).where(
            MCPPersonalAccessToken.user_id == self.role.user_id,
            MCPPersonalAccessToken.organization_id == self.organization_id,
        )

        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as exc:
                raise TracecatValidationError(
                    "Invalid cursor for MCP personal access tokens"
                ) from exc

            cursor_created_at = cursor_data.sort_value
            if not isinstance(cursor_created_at, datetime):
                raise TracecatValidationError(
                    "Invalid cursor for MCP personal access tokens"
                )

            if params.reverse:
                stmt = stmt.where(
                    sa.or_(
                        MCPPersonalAccessToken.created_at > cursor_created_at,
                        sa.and_(
                            MCPPersonalAccessToken.created_at == cursor_created_at,
                            MCPPersonalAccessToken.id > cursor_id,
                        ),
                    )
                )
            else:
                stmt = stmt.where(
                    sa.or_(
                        MCPPersonalAccessToken.created_at < cursor_created_at,
                        sa.and_(
                            MCPPersonalAccessToken.created_at == cursor_created_at,
                            MCPPersonalAccessToken.id < cursor_id,
                        ),
                    )
                )

        if params.reverse:
            stmt = stmt.order_by(
                MCPPersonalAccessToken.created_at.asc(),
                MCPPersonalAccessToken.id.asc(),
            )
        else:
            stmt = stmt.order_by(
                MCPPersonalAccessToken.created_at.desc(),
                MCPPersonalAccessToken.id.desc(),
            )

        count_stmt = (
            select(func.count())
            .select_from(MCPPersonalAccessToken)
            .where(
                MCPPersonalAccessToken.user_id == self.role.user_id,
                MCPPersonalAccessToken.organization_id == self.organization_id,
            )
        )

        result = await self.session.execute(stmt.limit(params.limit + 1))
        items = list(result.scalars().all())
        has_more = len(items) > params.limit
        if has_more:
            items = items[: params.limit]

        next_cursor = None
        prev_cursor = None
        if items:
            last = items[-1]
            next_cursor = (
                paginator.encode_cursor(
                    last.id,
                    sort_column="created_at",
                    sort_value=last.created_at,
                )
                if has_more
                else None
            )
            if params.cursor:
                first = items[0]
                prev_cursor = paginator.encode_cursor(
                    first.id,
                    sort_column="created_at",
                    sort_value=first.created_at,
                )

        if params.reverse:
            items.reverse()
            next_cursor, prev_cursor = prev_cursor, next_cursor
            has_more, has_previous = params.cursor is not None, has_more
        else:
            has_previous = params.cursor is not None

        return CursorPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=int(await self.session.scalar(count_stmt) or 0),
        )

    @require_scope("org:read")
    async def create_token(
        self,
        *,
        name: str,
        workspace_id: uuid.UUID | None,
        expires_at: datetime | None,
    ) -> IssuedMCPPersonalAccessTokenResult:
        if workspace_id is not None and not await _user_can_access_workspace(
            self.session,
            user_id=self.role.user_id,
            organization_id=self.organization_id,
            workspace_id=workspace_id,
        ):
            raise TracecatAuthorizationError("User cannot access workspace")

        generated = generate_managed_api_key(prefix=MCP_PAT_PREFIX)
        token = MCPPersonalAccessToken(
            id=uuid.uuid4(),
            user_id=self.role.user_id,
            organization_id=self.organization_id,
            workspace_id=workspace_id,
            name=name,
            key_id=generated.key_id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=generated.preview(),
            expires_at=expires_at,
            created_by=self.role.user_id,
        )
        self.session.add(token)
        await self.session.commit()
        await self.session.refresh(token)
        return IssuedMCPPersonalAccessTokenResult(token=token, raw_token=generated.raw)

    async def get_token(
        self,
        token_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> MCPPersonalAccessToken:
        stmt = select(MCPPersonalAccessToken).where(
            MCPPersonalAccessToken.id == token_id,
            MCPPersonalAccessToken.user_id == self.role.user_id,
            MCPPersonalAccessToken.organization_id == self.organization_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        if (token := result.scalar_one_or_none()) is None:
            raise TracecatNotFoundError("MCP personal access token not found")
        return token

    @require_scope("org:read")
    async def revoke_token(self, token_id: uuid.UUID) -> None:
        token = await self.get_token(token_id, for_update=True)
        if token.revoked_at is not None:
            return
        token.revoked_at = datetime.now(UTC)
        token.revoked_by = self.role.user_id
        await self.session.commit()


async def verify_mcp_personal_access_token(raw_token: str) -> MCPPATIdentity | None:
    """Verify an MCP personal access token and return its resolved identity."""
    parsed = parse_managed_api_key(raw_token, prefixes=(MCP_PAT_PREFIX,))
    if parsed is None:
        return None

    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = (
            select(MCPPersonalAccessToken, User)
            .join(User, MCPPersonalAccessToken.user_id == User.id)
            .where(MCPPersonalAccessToken.key_id == parsed.key_id)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None

        record, user = row
        if record.revoked_at is not None:
            return None
        if record.expires_at is not None:
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            else:
                expires_at = expires_at.astimezone(UTC)
            if expires_at <= datetime.now(UTC):
                return None
        if not user.is_active:
            return None
        if not verify_api_key(raw_token, record.salt, record.hashed):
            return None
        if not user.is_superuser:
            result = await session.execute(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.user_id == user.id,
                    OrganizationMembership.organization_id == record.organization_id,
                )
            )
            if result.scalar_one_or_none() is None:
                return None
        if record.workspace_id is not None and not await _user_can_access_workspace(
            session,
            user_id=user.id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
        ):
            return None

        record.last_used_at = datetime.now(UTC)
        await session.commit()
        return MCPPATIdentity(
            key_id=record.key_id,
            user_id=record.user_id,
            email=user.email,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            expires_at=record.expires_at,
        )
