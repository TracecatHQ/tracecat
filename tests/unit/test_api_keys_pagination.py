from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api_keys.service import OrganizationApiKeyService, _paginate_keys
from tracecat.auth.types import Role
from tracecat.db.models import OrganizationApiKey
from tracecat.pagination import CursorPaginationParams


class _ScalarResult:
    def all(self) -> list[OrganizationApiKey]:
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
async def test_paginate_keys_uses_ascending_order_for_reverse() -> None:
    session = _RecordingSession()

    await _paginate_keys(
        cast(AsyncSession, session),
        stmt=select(OrganizationApiKey),
        model=OrganizationApiKey,
        params=CursorPaginationParams(limit=20, reverse=True),
        total_estimate=0,
    )

    compiled = str(session.last_stmt.compile())

    assert "organization_api_key.created_at ASC" in compiled
    assert "organization_api_key.id ASC" in compiled


@pytest.mark.anyio
async def test_organization_list_keys_uses_filtered_total_count() -> None:
    session = _RecordingSession()
    service = OrganizationApiKeyService(
        cast(AsyncSession, session),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:api_key:read"}),
        ),
    )

    page = await service.list_keys(CursorPaginationParams(limit=20))

    compiled_count = str(session.last_scalar_stmt.compile())
    assert "count(*)" in compiled_count.lower()
    assert "WHERE organization_api_key.organization_id =" in compiled_count
    assert page.total_estimate == 7
