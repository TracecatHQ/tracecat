from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.audit.logger import audit_log
from tracecat.auth.api_keys import (
    ORG_API_KEY_PREFIX,
    WORKSPACE_API_KEY_PREFIX,
    generate_managed_api_key,
)
from tracecat.auth.credentials import compute_effective_scopes
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.models import Scope, ServiceAccount, ServiceAccountApiKey
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
from tracecat.service_accounts.constants import (
    is_org_service_account_assignable_scope,
    is_workspace_service_account_assignable_scope,
)

ServiceAccountScopeValidator = Callable[..., bool]


class CreatedCursorColumns(NamedTuple):
    id: sa.SQLColumnExpression[uuid.UUID]
    created_at: sa.SQLColumnExpression[datetime]


class IssuedServiceAccountApiKeyResult(NamedTuple):
    service_account: ServiceAccount
    api_key: ServiceAccountApiKey
    raw_key: str

    @property
    def api_key_id(self) -> uuid.UUID:
        return self.api_key.id


def _apply_created_cursor_filter[T](
    stmt: sa.Select[tuple[T]],
    *,
    params: CursorPaginationParams,
    columns: CreatedCursorColumns,
    invalid_cursor_message: str,
) -> sa.Select[tuple[T]]:
    if not params.cursor:
        return stmt

    try:
        cursor_data = BaseCursorPaginator.decode_cursor(params.cursor)
        cursor_id = uuid.UUID(cursor_data.id)
    except ValueError as exc:
        raise TracecatValidationError(invalid_cursor_message) from exc
    cursor_sort_value = cursor_data.sort_value
    if not isinstance(cursor_sort_value, datetime):
        raise TracecatValidationError(invalid_cursor_message)

    if params.reverse:
        return stmt.where(
            sa.or_(
                columns.created_at > cursor_sort_value,
                sa.and_(
                    columns.created_at == cursor_sort_value,
                    columns.id > cursor_id,
                ),
            )
        )

    return stmt.where(
        sa.or_(
            columns.created_at < cursor_sort_value,
            sa.and_(
                columns.created_at == cursor_sort_value,
                columns.id < cursor_id,
            ),
        )
    )


def _apply_created_cursor(
    stmt: sa.Select[tuple[ServiceAccount]],
    *,
    params: CursorPaginationParams,
) -> sa.Select[tuple[ServiceAccount]]:
    return _apply_created_cursor_filter(
        stmt,
        params=params,
        columns=CreatedCursorColumns(
            id=ServiceAccount.id,
            created_at=ServiceAccount.created_at,
        ),
        invalid_cursor_message="Invalid cursor for service accounts",
    )


async def _paginate_created_models[T](
    session: AsyncSession,
    *,
    stmt: sa.Select[tuple[T]],
    params: CursorPaginationParams,
    total_estimate: int,
    columns: CreatedCursorColumns,
    get_id: Callable[[T], uuid.UUID],
    get_created_at: Callable[[T], datetime],
    apply_cursor: Callable[[sa.Select[tuple[T]]], sa.Select[tuple[T]]],
) -> CursorPaginatedResponse[T]:
    if params.reverse:
        ordered_stmt = stmt.order_by(columns.created_at.asc(), columns.id.asc())
    else:
        ordered_stmt = stmt.order_by(columns.created_at.desc(), columns.id.desc())
    paged_stmt = apply_cursor(ordered_stmt).limit(params.limit + 1)
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
                get_id(last),
                sort_column="created_at",
                sort_value=get_created_at(last),
            )
            if has_more
            else None
        )
        if params.cursor is not None:
            first = items[0]
            prev_cursor = BaseCursorPaginator.encode_cursor(
                get_id(first),
                sort_column="created_at",
                sort_value=get_created_at(first),
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
        total_estimate=total_estimate,
    )


async def _paginate_service_accounts(
    session: AsyncSession,
    *,
    stmt: sa.Select[tuple[ServiceAccount]],
    params: CursorPaginationParams,
    total_estimate: int,
) -> CursorPaginatedResponse[ServiceAccount]:
    return await _paginate_created_models(
        session,
        stmt=stmt,
        params=params,
        total_estimate=total_estimate,
        columns=CreatedCursorColumns(
            id=ServiceAccount.id,
            created_at=ServiceAccount.created_at,
        ),
        get_id=lambda item: item.id,
        get_created_at=lambda item: item.created_at,
        apply_cursor=lambda ordered_stmt: _apply_created_cursor(
            ordered_stmt, params=params
        ),
    )


def _apply_api_key_created_cursor(
    stmt: sa.Select[tuple[ServiceAccountApiKey]],
    *,
    params: CursorPaginationParams,
) -> sa.Select[tuple[ServiceAccountApiKey]]:
    return _apply_created_cursor_filter(
        stmt,
        params=params,
        columns=CreatedCursorColumns(
            id=ServiceAccountApiKey.id,
            created_at=ServiceAccountApiKey.created_at,
        ),
        invalid_cursor_message="Invalid cursor for service account API keys",
    )


async def _paginate_service_account_api_keys(
    session: AsyncSession,
    *,
    stmt: sa.Select[tuple[ServiceAccountApiKey]],
    params: CursorPaginationParams,
    total_estimate: int,
) -> CursorPaginatedResponse[ServiceAccountApiKey]:
    return await _paginate_created_models(
        session,
        stmt=stmt,
        params=params,
        total_estimate=total_estimate,
        columns=CreatedCursorColumns(
            id=ServiceAccountApiKey.id,
            created_at=ServiceAccountApiKey.created_at,
        ),
        get_id=lambda item: item.id,
        get_created_at=lambda item: item.created_at,
        apply_cursor=lambda ordered_stmt: _apply_api_key_created_cursor(
            ordered_stmt, params=params
        ),
    )


def get_active_service_account_key(
    service_account: ServiceAccount,
) -> ServiceAccountApiKey | None:
    active_keys = [key for key in service_account.api_keys if key.revoked_at is None]
    if not active_keys:
        return None
    return max(active_keys, key=lambda key: (key.created_at, key.id))


def get_service_account_last_used_at(
    service_account: ServiceAccount,
) -> datetime | None:
    last_used_candidates = [
        key.last_used_at
        for key in service_account.api_keys
        if key.last_used_at is not None
    ]
    if not last_used_candidates:
        return None
    return max(last_used_candidates)


def get_service_account_api_key_counts(
    service_account: ServiceAccount,
) -> tuple[int, int, int]:
    total = len(service_account.api_keys)
    active = sum(key.revoked_at is None for key in service_account.api_keys)
    revoked = total - active
    return total, active, revoked


async def _ensure_role_can_assign_scopes(role: Role, scopes: list[Scope]) -> None:
    if not scopes:
        return

    effective_scopes = await compute_effective_scopes(role)
    denied = [
        scope.name for scope in scopes if not has_scope(effective_scopes, scope.name)
    ]
    if denied:
        joined = ", ".join(sorted(denied))
        raise TracecatAuthorizationError(
            f"Cannot assign service account scopes not held by the caller: {joined}"
        )


async def _resolve_assignable_scopes(
    session: AsyncSession,
    *,
    scope_ids: list[uuid.UUID],
    validator: ServiceAccountScopeValidator,
) -> list[Scope]:
    if not scope_ids:
        return []

    stmt = select(Scope).where(Scope.id.in_(scope_ids))
    result = await session.execute(stmt)
    scopes = list(result.scalars().all())
    if len(scopes) != len(set(scope_ids)):
        raise TracecatValidationError("One or more scopes were not found")

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
        raise TracecatValidationError(
            f"Unsupported service account scopes requested: {joined}"
        )
    return scopes


class BaseServiceAccountService:
    role: Role
    session: AsyncSession
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    key_prefix: str
    workspace_scoped = False
    not_found_message = "Service account not found"
    scope_validator: ServiceAccountScopeValidator

    def _service_account_filters(self) -> tuple[sa.ColumnElement[bool], ...]:
        if self.workspace_scoped:
            return (ServiceAccount.workspace_id == self.workspace_id,)
        return (
            ServiceAccount.organization_id == self.organization_id,
            ServiceAccount.workspace_id.is_(None),
        )

    def _service_account_stmt(
        self, service_account_id: uuid.UUID
    ) -> sa.Select[tuple[ServiceAccount]]:
        return (
            select(ServiceAccount)
            .where(
                ServiceAccount.id == service_account_id,
                *self._service_account_filters(),
            )
            .options(
                selectinload(ServiceAccount.scopes),
                selectinload(ServiceAccount.api_keys),
            )
        )

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
            if self.scope_validator(
                name=scope.name,
                source=scope.source,
                organization_id_present=scope.organization_id is not None,
            )
        ]

    async def _list_service_accounts(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[ServiceAccount]:
        stmt = (
            select(ServiceAccount)
            .where(*self._service_account_filters())
            .options(
                selectinload(ServiceAccount.scopes),
                selectinload(ServiceAccount.api_keys),
            )
        )
        count_stmt = (
            select(func.count())
            .select_from(ServiceAccount)
            .where(*self._service_account_filters())
        )
        return await _paginate_service_accounts(
            self.session,
            stmt=stmt,
            params=params,
            total_estimate=int(await self.session.scalar(count_stmt) or 0),
        )

    async def get_service_account(
        self, service_account_id: uuid.UUID
    ) -> ServiceAccount:
        stmt = self._service_account_stmt(service_account_id)
        result = await self.session.execute(stmt)
        if (service_account := result.scalar_one_or_none()) is None:
            raise TracecatNotFoundError(self.not_found_message)
        return service_account

    async def _create_api_key(
        self,
        *,
        service_account: ServiceAccount,
        name: str,
    ) -> tuple[ServiceAccountApiKey, str]:
        generated = generate_managed_api_key(prefix=self.key_prefix)
        api_key = ServiceAccountApiKey(
            service_account_id=service_account.id,
            name=name,
            key_id=generated.key_id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=generated.preview(),
            created_by=self.role.user_id,
        )
        self.session.add(api_key)
        return api_key, generated.raw

    async def _resolve_scopes(self, scope_ids: list[uuid.UUID]) -> list[Scope]:
        return await _resolve_assignable_scopes(
            self.session,
            scope_ids=scope_ids,
            validator=self.scope_validator,
        )

    async def _create_service_account(
        self,
        *,
        name: str,
        description: str | None,
        scope_ids: list[uuid.UUID],
        initial_key_name: str,
    ) -> tuple[ServiceAccount, ServiceAccountApiKey, str]:
        scopes = await self._resolve_scopes(scope_ids)
        await _ensure_role_can_assign_scopes(self.role, scopes)
        service_account = ServiceAccount(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id if self.workspace_scoped else None,
            name=name,
            description=description,
            owner_user_id=self.role.user_id,
            scopes=scopes,
        )
        self.session.add(service_account)
        await self.session.flush()
        created_api_key, raw_key = await self._create_api_key(
            service_account=service_account,
            name=initial_key_name,
        )
        await self.session.commit()
        refreshed_service_account = await self.get_service_account(service_account.id)
        issued_api_key = next(
            key
            for key in refreshed_service_account.api_keys
            if key.id == created_api_key.id
        )
        return refreshed_service_account, issued_api_key, raw_key

    async def _update_service_account(
        self,
        service_account_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        description_provided: bool,
        scope_ids: list[uuid.UUID] | None,
    ) -> ServiceAccount:
        service_account = await self.get_service_account(service_account_id)
        if name is not None:
            service_account.name = name
        if description_provided:
            service_account.description = description
        if scope_ids is not None:
            scopes = await self._resolve_scopes(scope_ids)
            await _ensure_role_can_assign_scopes(self.role, scopes)
            service_account.scopes = scopes
        await self.session.commit()
        return await self.get_service_account(service_account.id)

    async def _set_service_account_disabled(
        self, service_account_id: uuid.UUID, *, disabled: bool
    ) -> None:
        service_account = await self.get_service_account(service_account_id)
        if disabled and service_account.disabled_at is None:
            service_account.disabled_at = datetime.now(UTC)
            await self.session.commit()
        elif not disabled and service_account.disabled_at is not None:
            service_account.disabled_at = None
            await self.session.commit()

    async def _list_service_account_api_keys(
        self,
        service_account_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[ServiceAccountApiKey]:
        await self.get_service_account(service_account_id)
        stmt = select(ServiceAccountApiKey).where(
            ServiceAccountApiKey.service_account_id == service_account_id
        )
        count_stmt = (
            select(func.count())
            .select_from(ServiceAccountApiKey)
            .where(ServiceAccountApiKey.service_account_id == service_account_id)
        )
        return await _paginate_service_account_api_keys(
            self.session,
            stmt=stmt,
            params=params,
            total_estimate=int(await self.session.scalar(count_stmt) or 0),
        )

    async def _get_service_account_api_key(
        self,
        service_account_id: uuid.UUID,
        api_key_id: uuid.UUID,
    ) -> ServiceAccountApiKey:
        service_account = await self.get_service_account(service_account_id)
        for api_key in service_account.api_keys:
            if api_key.id == api_key_id:
                return api_key
        raise TracecatNotFoundError("Service account API key not found")

    async def _issue_api_key(
        self,
        service_account_id: uuid.UUID,
        *,
        name: str,
    ) -> IssuedServiceAccountApiKeyResult:
        service_account = await self.get_service_account(service_account_id)
        if service_account.disabled_at is not None:
            raise TracecatAuthorizationError(
                "Disabled service accounts cannot generate new API keys"
            )
        if active_key := get_active_service_account_key(service_account):
            active_key.revoked_at = datetime.now(UTC)
            active_key.revoked_by = self.role.user_id
        created_api_key, raw_key = await self._create_api_key(
            service_account=service_account, name=name
        )
        await self.session.commit()
        refreshed_service_account = await self.get_service_account(service_account.id)
        issued_api_key = next(
            key
            for key in refreshed_service_account.api_keys
            if key.id == created_api_key.id
        )
        return IssuedServiceAccountApiKeyResult(
            service_account=refreshed_service_account,
            api_key=issued_api_key,
            raw_key=raw_key,
        )

    async def _revoke_api_key(
        self,
        service_account_id: uuid.UUID,
        api_key_id: uuid.UUID,
    ) -> None:
        api_key = await self._get_service_account_api_key(
            service_account_id, api_key_id
        )
        if api_key.revoked_at is not None:
            return
        api_key.revoked_at = datetime.now(UTC)
        api_key.revoked_by = self.role.user_id
        await self.session.commit()


class OrganizationServiceAccountService(BaseOrgService, BaseServiceAccountService):
    service_name = "organization_service_accounts"
    key_prefix = ORG_API_KEY_PREFIX
    not_found_message = "Organization service account not found"
    scope_validator = staticmethod(is_org_service_account_assignable_scope)

    @require_scope("org:service_account:read")
    async def list_service_accounts(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[ServiceAccount]:
        return await self._list_service_accounts(params)

    @require_scope("org:service_account:read")
    async def list_service_account_api_keys(
        self,
        service_account_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[ServiceAccountApiKey]:
        return await self._list_service_account_api_keys(service_account_id, params)

    @require_scope("org:service_account:create")
    @audit_log(resource_type="service_account", action="create")
    async def create_service_account(
        self,
        *,
        name: str,
        description: str | None,
        scope_ids: list[uuid.UUID],
        initial_key_name: str,
    ) -> tuple[ServiceAccount, ServiceAccountApiKey, str]:
        return await self._create_service_account(
            name=name,
            description=description,
            scope_ids=scope_ids,
            initial_key_name=initial_key_name,
        )

    @require_scope("org:service_account:update")
    @audit_log(
        resource_type="service_account",
        action="update",
        resource_id_attr="service_account_id",
    )
    async def update_service_account(
        self,
        service_account_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        description_provided: bool,
        scope_ids: list[uuid.UUID] | None,
    ) -> ServiceAccount:
        return await self._update_service_account(
            service_account_id,
            name=name,
            description=description,
            description_provided=description_provided,
            scope_ids=scope_ids,
        )

    @require_scope("org:service_account:disable")
    @audit_log(
        resource_type="service_account",
        action="update",
        resource_id_attr="service_account_id",
    )
    async def disable_service_account(self, service_account_id: uuid.UUID) -> None:
        await self._set_service_account_disabled(service_account_id, disabled=True)

    @require_scope("org:service_account:disable")
    @audit_log(
        resource_type="service_account",
        action="update",
        resource_id_attr="service_account_id",
    )
    async def enable_service_account(self, service_account_id: uuid.UUID) -> None:
        await self._set_service_account_disabled(service_account_id, disabled=False)

    @require_scope("org:service_account:update")
    @audit_log(
        resource_type="service_account_api_key",
        action="create",
        resource_id_attr="api_key_id",
    )
    async def issue_api_key(
        self,
        service_account_id: uuid.UUID,
        *,
        name: str,
    ) -> IssuedServiceAccountApiKeyResult:
        return await self._issue_api_key(service_account_id, name=name)

    @require_scope("org:service_account:update")
    @audit_log(
        resource_type="service_account_api_key",
        action="revoke",
        resource_id_attr="api_key_id",
    )
    async def revoke_api_key(
        self,
        service_account_id: uuid.UUID,
        api_key_id: uuid.UUID,
    ) -> None:
        await self._revoke_api_key(service_account_id, api_key_id)


class WorkspaceServiceAccountService(BaseWorkspaceService, BaseServiceAccountService):
    service_name = "workspace_service_accounts"
    key_prefix = WORKSPACE_API_KEY_PREFIX
    workspace_scoped = True
    not_found_message = "Workspace service account not found"
    scope_validator = staticmethod(is_workspace_service_account_assignable_scope)

    @require_scope("workspace:service_account:read")
    async def list_service_accounts(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[ServiceAccount]:
        return await self._list_service_accounts(params)

    @require_scope("workspace:service_account:read")
    async def list_service_account_api_keys(
        self,
        service_account_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[ServiceAccountApiKey]:
        return await self._list_service_account_api_keys(service_account_id, params)

    @require_scope("workspace:service_account:create")
    @audit_log(resource_type="service_account", action="create")
    async def create_service_account(
        self,
        *,
        name: str,
        description: str | None,
        scope_ids: list[uuid.UUID],
        initial_key_name: str,
    ) -> tuple[ServiceAccount, ServiceAccountApiKey, str]:
        return await self._create_service_account(
            name=name,
            description=description,
            scope_ids=scope_ids,
            initial_key_name=initial_key_name,
        )

    @require_scope("workspace:service_account:update")
    @audit_log(
        resource_type="service_account",
        action="update",
        resource_id_attr="service_account_id",
    )
    async def update_service_account(
        self,
        service_account_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        description_provided: bool,
        scope_ids: list[uuid.UUID] | None,
    ) -> ServiceAccount:
        return await self._update_service_account(
            service_account_id,
            name=name,
            description=description,
            description_provided=description_provided,
            scope_ids=scope_ids,
        )

    @require_scope("workspace:service_account:disable")
    @audit_log(
        resource_type="service_account",
        action="update",
        resource_id_attr="service_account_id",
    )
    async def disable_service_account(self, service_account_id: uuid.UUID) -> None:
        await self._set_service_account_disabled(service_account_id, disabled=True)

    @require_scope("workspace:service_account:disable")
    @audit_log(
        resource_type="service_account",
        action="update",
        resource_id_attr="service_account_id",
    )
    async def enable_service_account(self, service_account_id: uuid.UUID) -> None:
        await self._set_service_account_disabled(service_account_id, disabled=False)

    @require_scope("workspace:service_account:update")
    @audit_log(
        resource_type="service_account_api_key",
        action="create",
        resource_id_attr="api_key_id",
    )
    async def issue_api_key(
        self,
        service_account_id: uuid.UUID,
        *,
        name: str,
    ) -> IssuedServiceAccountApiKeyResult:
        return await self._issue_api_key(service_account_id, name=name)

    @require_scope("workspace:service_account:update")
    @audit_log(
        resource_type="service_account_api_key",
        action="revoke",
        resource_id_attr="api_key_id",
    )
    async def revoke_api_key(
        self,
        service_account_id: uuid.UUID,
        api_key_id: uuid.UUID,
    ) -> None:
        await self._revoke_api_key(service_account_id, api_key_id)
