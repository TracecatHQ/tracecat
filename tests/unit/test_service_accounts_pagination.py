from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import ServiceAccount
from tracecat.pagination import CursorPaginationParams
from tracecat.service_accounts.service import (
    OrganizationServiceAccountService,
)
from tracecat.tiers import defaults as tier_defaults


@pytest.fixture(autouse=True)
def enable_service_accounts_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
            update={"service_accounts": True}
        ),
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
