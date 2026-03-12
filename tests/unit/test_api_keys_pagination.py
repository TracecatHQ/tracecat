from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api_keys.service import _paginate_keys
from tracecat.db.models import OrganizationApiKey
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams


class _ScalarResult:
    def all(self) -> list[OrganizationApiKey]:
        return []


class _ExecuteResult:
    def scalars(self) -> _ScalarResult:
        return _ScalarResult()


class _RecordingSession:
    def __init__(self) -> None:
        self.last_stmt: Any = None

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.last_stmt = stmt
        return _ExecuteResult()


@pytest.mark.anyio
async def test_paginate_keys_uses_ascending_order_for_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _RecordingSession()

    async def _row_estimate(self, _table_name: str) -> int:
        return 0

    monkeypatch.setattr(
        BaseCursorPaginator,
        "get_table_row_estimate",
        _row_estimate,
    )

    await _paginate_keys(
        cast(AsyncSession, session),
        stmt=select(OrganizationApiKey),
        model=OrganizationApiKey,
        params=CursorPaginationParams(limit=20, reverse=True),
    )

    compiled = str(session.last_stmt.compile())

    assert "organization_api_key.created_at ASC" in compiled
    assert "organization_api_key.id ASC" in compiled
