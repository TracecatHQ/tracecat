from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import ServiceAccount, ServiceAccountApiKey
from tracecat.pagination import CursorPaginationParams
from tracecat.service_accounts.service import (
    OrganizationServiceAccountService,
    _paginate_service_account_api_keys,
    _paginate_service_accounts,
)


class _ScalarResult:
    def all(self) -> list[ServiceAccount]:
        return []


class _ExecuteResult:
    def scalars(self) -> _ScalarResult:
        return _ScalarResult()


class _RecordingSession:
    def __init__(self) -> None:
        self.last_stmt: Any = None
        self.last_scalar_stmt: Any = None

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.last_stmt = stmt
        return _ExecuteResult()

    async def scalar(self, stmt: Any) -> int:
        self.last_scalar_stmt = stmt
        return 7


@pytest.mark.anyio
async def test_paginate_service_accounts_uses_ascending_order_for_reverse() -> None:
    session = _RecordingSession()

    await _paginate_service_accounts(
        cast(AsyncSession, session),
        stmt=select(ServiceAccount),
        params=CursorPaginationParams(limit=20, reverse=True),
        total_estimate=0,
    )

    compiled = str(session.last_stmt.compile())

    assert "service_account.created_at ASC" in compiled
    assert "service_account.id ASC" in compiled


@pytest.mark.anyio
async def test_paginate_service_account_api_keys_uses_ascending_order_for_reverse() -> (
    None
):
    session = _RecordingSession()

    await _paginate_service_account_api_keys(
        cast(AsyncSession, session),
        stmt=select(ServiceAccountApiKey),
        params=CursorPaginationParams(limit=20, reverse=True),
        total_estimate=0,
    )

    compiled = str(session.last_stmt.compile())

    assert "service_account_api_key.created_at ASC" in compiled
    assert "service_account_api_key.id ASC" in compiled


@pytest.mark.anyio
async def test_organization_list_uses_filtered_total_count() -> None:
    session = _RecordingSession()
    organization_id = uuid.uuid4()
    service = OrganizationServiceAccountService(
        cast(AsyncSession, session),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            organization_id=organization_id,
            scopes=frozenset({"org:service_account:read"}),
        ),
    )

    page = await service.list_service_accounts(CursorPaginationParams(limit=20))

    compiled_count = str(session.last_scalar_stmt.compile())
    assert "count(*)" in compiled_count.lower()
    assert "WHERE service_account.organization_id =" in compiled_count
    assert "service_account.workspace_id IS NULL" in compiled_count
    assert page.total_estimate == 7
