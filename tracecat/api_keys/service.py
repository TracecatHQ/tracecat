from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from tracecat.api_keys.constants import (
    ORG_API_KEY_KIND,
    ORG_API_KEY_PREFIX,
    WORKSPACE_API_KEY_KIND,
    WORKSPACE_API_KEY_PREFIX,
    APIKeyKind,
    is_org_api_key_assignable_scope,
    is_workspace_api_key_assignable_scope,
)
from tracecat.audit.logger import audit_log
from tracecat.auth.api_keys import generate_managed_api_key
from tracecat.auth.credentials import compute_effective_scopes
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.models import OrganizationApiKey, Scope, WorkspaceApiKey
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseOrgService, BaseWorkspaceService


def _apply_created_cursor(
    stmt: sa.Select[Any],
    *,
    model: type[OrganizationApiKey] | type[WorkspaceApiKey],
    params: CursorPaginationParams,
) -> sa.Select[Any]:
    if not params.cursor:
        return stmt

    try:
        cursor_data = BaseCursorPaginator.decode_cursor(params.cursor)
        cursor_id = uuid.UUID(cursor_data.id)
    except ValueError as exc:
        raise TracecatValidationError("Invalid cursor for API keys") from exc
    cursor_sort_value = cursor_data.sort_value
    if not isinstance(cursor_sort_value, datetime):
        raise TracecatValidationError("Invalid cursor for API keys")

    created_at = cast(InstrumentedAttribute[datetime], model.created_at)
    id_col = cast(InstrumentedAttribute[uuid.UUID], model.id)
    if params.reverse:
        return stmt.where(
            sa.or_(
                created_at > cursor_sort_value,
                sa.and_(created_at == cursor_sort_value, id_col > cursor_id),
            )
        )

    return stmt.where(
        sa.or_(
            created_at < cursor_sort_value,
            sa.and_(created_at == cursor_sort_value, id_col < cursor_id),
        )
    )


async def _paginate_keys[T: OrganizationApiKey | WorkspaceApiKey](
    session: AsyncSession,
    *,
    stmt: sa.Select[Any],
    model: type[T],
    params: CursorPaginationParams,
) -> CursorPaginatedResponse[T]:
    if params.reverse:
        ordered_stmt = stmt.order_by(model.created_at.asc(), model.id.asc())
    else:
        ordered_stmt = stmt.order_by(model.created_at.desc(), model.id.desc())
    paged_stmt = _apply_created_cursor(ordered_stmt, model=model, params=params).limit(
        params.limit + 1
    )
    result = await session.execute(paged_stmt)
    items = list(result.scalars().all())
    has_more = len(items) > params.limit
    if has_more:
        items = items[: params.limit]

    next_cursor = None
    prev_cursor = None
    if items:
        last = items[-1]
        next_cursor = (
            BaseCursorPaginator.encode_cursor(
                last.id,
                sort_column="created_at",
                sort_value=last.created_at,
            )
            if has_more
            else None
        )
        if params.cursor is not None:
            first = items[0]
            prev_cursor = BaseCursorPaginator.encode_cursor(
                first.id,
                sort_column="created_at",
                sort_value=first.created_at,
            )

    if params.reverse:
        items.reverse()
        next_cursor, prev_cursor = prev_cursor, next_cursor

    return CursorPaginatedResponse(
        items=items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=has_more,
        has_previous=params.cursor is not None,
        total_estimate=await BaseCursorPaginator(session).get_table_row_estimate(
            model.__tablename__
        ),
    )


async def _ensure_role_can_assign_scopes(
    role: Role, scopes: list[Scope], *, kind: APIKeyKind
) -> None:
    if not scopes:
        return

    effective_scopes = await compute_effective_scopes(role)
    denied = [
        scope.name for scope in scopes if not has_scope(effective_scopes, scope.name)
    ]
    if denied:
        joined = ", ".join(sorted(denied))
        raise TracecatAuthorizationError(
            f"Cannot assign {kind} API key scopes not held by the caller: {joined}"
        )


class OrganizationApiKeyService(BaseOrgService):
    service_name = "organization_api_keys"

    async def list_assignable_scopes(self) -> list[Scope]:
        stmt = (
            select(Scope)
            .where(
                Scope.source == "platform",
                Scope.organization_id.is_(None),
            )
            .order_by(Scope.name)
        )
        result = await self.session.execute(stmt)
        scopes = result.scalars().all()
        return [
            scope
            for scope in scopes
            if is_org_api_key_assignable_scope(
                name=scope.name,
                source=scope.source,
                organization_id_present=scope.organization_id is not None,
            )
        ]

    async def list_keys(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[OrganizationApiKey]:
        stmt = (
            select(OrganizationApiKey)
            .where(OrganizationApiKey.organization_id == self.organization_id)
            .options(selectinload(OrganizationApiKey.scopes))
        )
        return await _paginate_keys(
            self.session,
            stmt=stmt,
            model=OrganizationApiKey,
            params=params,
        )

    async def get_key(self, api_key_id: uuid.UUID) -> OrganizationApiKey:
        stmt = (
            select(OrganizationApiKey)
            .where(
                OrganizationApiKey.id == api_key_id,
                OrganizationApiKey.organization_id == self.organization_id,
            )
            .options(selectinload(OrganizationApiKey.scopes))
        )
        result = await self.session.execute(stmt)
        if (api_key := result.scalar_one_or_none()) is None:
            raise TracecatNotFoundError("Organization API key not found")
        return api_key

    @require_scope("org:api_key:create")
    @audit_log(resource_type="organization_api_key", action="create")
    async def create_key(
        self,
        *,
        name: str,
        description: str | None,
        scope_ids: list[uuid.UUID],
    ) -> tuple[OrganizationApiKey, str]:
        scopes = await self._resolve_assignable_scopes(scope_ids)
        await _ensure_role_can_assign_scopes(self.role, scopes, kind=ORG_API_KEY_KIND)
        generated = generate_managed_api_key(prefix=ORG_API_KEY_PREFIX)
        api_key = OrganizationApiKey(
            organization_id=self.organization_id,
            name=name,
            description=description,
            key_id=generated.key_id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=generated.preview(),
            created_by=self.role.user_id,
            scopes=scopes,
        )
        self.session.add(api_key)
        await self.session.commit()
        await self.session.refresh(api_key, ["scopes"])
        return api_key, generated.raw

    @require_scope("org:api_key:update")
    @audit_log(
        resource_type="organization_api_key",
        action="update",
        resource_id_attr="api_key_id",
    )
    async def update_key(
        self,
        api_key_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        description_provided: bool,
        scope_ids: list[uuid.UUID] | None,
    ) -> OrganizationApiKey:
        api_key = await self.get_key(api_key_id)
        if api_key.revoked_at is not None:
            raise TracecatAuthorizationError("Revoked API keys cannot be updated")
        if name is not None:
            api_key.name = name
        if description_provided:
            api_key.description = description
        if scope_ids is not None:
            scopes = await self._resolve_assignable_scopes(scope_ids)
            await _ensure_role_can_assign_scopes(
                self.role, scopes, kind=ORG_API_KEY_KIND
            )
            api_key.scopes = scopes
        await self.session.commit()
        await self.session.refresh(api_key, ["scopes"])
        return api_key

    @require_scope("org:api_key:revoke")
    @audit_log(
        resource_type="organization_api_key",
        action="revoke",
        resource_id_attr="api_key_id",
    )
    async def revoke_key(self, api_key_id: uuid.UUID) -> None:
        api_key = await self.get_key(api_key_id)
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
            api_key.revoked_by = self.role.user_id
            await self.session.commit()

    async def _resolve_assignable_scopes(
        self, scope_ids: list[uuid.UUID]
    ) -> list[Scope]:
        return await _resolve_assignable_scopes(
            self.session,
            scope_ids=scope_ids,
            validator=is_org_api_key_assignable_scope,
            kind=ORG_API_KEY_KIND,
        )


class WorkspaceApiKeyService(BaseWorkspaceService):
    service_name = "workspace_api_keys"

    async def list_assignable_scopes(self) -> list[Scope]:
        stmt = (
            select(Scope)
            .where(
                Scope.source == "platform",
                Scope.organization_id.is_(None),
            )
            .order_by(Scope.name)
        )
        result = await self.session.execute(stmt)
        scopes = result.scalars().all()
        return [
            scope
            for scope in scopes
            if is_workspace_api_key_assignable_scope(
                name=scope.name,
                source=scope.source,
                organization_id_present=scope.organization_id is not None,
            )
        ]

    async def list_keys(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[WorkspaceApiKey]:
        stmt = (
            select(WorkspaceApiKey)
            .where(WorkspaceApiKey.workspace_id == self.workspace_id)
            .options(selectinload(WorkspaceApiKey.scopes))
        )
        return await _paginate_keys(
            self.session,
            stmt=stmt,
            model=WorkspaceApiKey,
            params=params,
        )

    async def get_key(self, api_key_id: uuid.UUID) -> WorkspaceApiKey:
        stmt = (
            select(WorkspaceApiKey)
            .where(
                WorkspaceApiKey.id == api_key_id,
                WorkspaceApiKey.workspace_id == self.workspace_id,
            )
            .options(selectinload(WorkspaceApiKey.scopes))
        )
        result = await self.session.execute(stmt)
        if (api_key := result.scalar_one_or_none()) is None:
            raise TracecatNotFoundError("Workspace API key not found")
        return api_key

    @require_scope("workspace:api_key:create")
    @audit_log(resource_type="workspace_api_key", action="create")
    async def create_key(
        self,
        *,
        name: str,
        description: str | None,
        scope_ids: list[uuid.UUID],
    ) -> tuple[WorkspaceApiKey, str]:
        scopes = await self._resolve_assignable_scopes(scope_ids)
        await _ensure_role_can_assign_scopes(
            self.role, scopes, kind=WORKSPACE_API_KEY_KIND
        )
        generated = generate_managed_api_key(prefix=WORKSPACE_API_KEY_PREFIX)
        api_key = WorkspaceApiKey(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=name,
            description=description,
            key_id=generated.key_id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=generated.preview(),
            created_by=self.role.user_id,
            scopes=scopes,
        )
        self.session.add(api_key)
        await self.session.commit()
        await self.session.refresh(api_key, ["scopes"])
        return api_key, generated.raw

    @require_scope("workspace:api_key:update")
    @audit_log(
        resource_type="workspace_api_key",
        action="update",
        resource_id_attr="api_key_id",
    )
    async def update_key(
        self,
        api_key_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        description_provided: bool,
        scope_ids: list[uuid.UUID] | None,
    ) -> WorkspaceApiKey:
        api_key = await self.get_key(api_key_id)
        if api_key.revoked_at is not None:
            raise TracecatAuthorizationError("Revoked API keys cannot be updated")
        if name is not None:
            api_key.name = name
        if description_provided:
            api_key.description = description
        if scope_ids is not None:
            scopes = await self._resolve_assignable_scopes(scope_ids)
            await _ensure_role_can_assign_scopes(
                self.role, scopes, kind=WORKSPACE_API_KEY_KIND
            )
            api_key.scopes = scopes
        await self.session.commit()
        await self.session.refresh(api_key, ["scopes"])
        return api_key

    @require_scope("workspace:api_key:revoke")
    @audit_log(
        resource_type="workspace_api_key",
        action="revoke",
        resource_id_attr="api_key_id",
    )
    async def revoke_key(self, api_key_id: uuid.UUID) -> None:
        api_key = await self.get_key(api_key_id)
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
            api_key.revoked_by = self.role.user_id
            await self.session.commit()

    async def _resolve_assignable_scopes(
        self, scope_ids: list[uuid.UUID]
    ) -> list[Scope]:
        return await _resolve_assignable_scopes(
            self.session,
            scope_ids=scope_ids,
            validator=is_workspace_api_key_assignable_scope,
            kind=WORKSPACE_API_KEY_KIND,
        )


async def _resolve_assignable_scopes(
    session: AsyncSession,
    *,
    scope_ids: list[uuid.UUID],
    validator: Callable[..., bool],
    kind: APIKeyKind,
) -> list[Scope]:
    if not scope_ids:
        return []

    stmt = select(Scope).where(Scope.id.in_(scope_ids))
    result = await session.execute(stmt)
    scopes = list(result.scalars().all())
    if len(scopes) != len(set(scope_ids)):
        raise TracecatNotFoundError("One or more scopes were not found")

    invalid = [
        scope.name
        for scope in scopes
        if not validator(
            name=scope.name,
            source=scope.source,
            organization_id_present=scope.organization_id is not None,
        )
    ]
    if invalid:
        joined = ", ".join(sorted(invalid))
        raise TracecatAuthorizationError(
            f"Scopes are not assignable to {kind} API keys: {joined}"
        )
    return sorted(scopes, key=lambda scope: scope.name)
