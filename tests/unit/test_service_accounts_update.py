from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tracecat.service_accounts.service as service_accounts_service
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.db.models import Scope
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.service_accounts.service import OrganizationServiceAccountService


class _NoopSession:
    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any, _attrs: list[str]) -> None:
        return None


@pytest.mark.anyio
async def test_update_service_account_can_clear_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_account = SimpleNamespace(
        id=uuid.uuid4(),
        name="Org automation",
        description="to be cleared",
        disabled_at=None,
        scopes=[],
    )
    service = OrganizationServiceAccountService(
        cast(AsyncSession, _NoopSession()),
        role=Role(
            type="user",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:service_account:update"}),
        ),
    )
    monkeypatch.setattr(
        service, "get_service_account", AsyncMock(return_value=service_account)
    )

    updated = await service.update_service_account(
        service_account.id,
        name=None,
        description=None,
        description_provided=True,
        scope_ids=None,
    )

    assert updated.description is None


@pytest.mark.anyio
async def test_create_service_account_rejects_scopes_not_held_by_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OrganizationServiceAccountService(
        cast(AsyncSession, _NoopSession()),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:service_account:create"}),
        ),
    )
    monkeypatch.setattr(
        service,
        "_resolve_scopes",
        AsyncMock(
            return_value=[
                Scope(
                    id=uuid.uuid4(),
                    name="workspace:delete",
                    resource="workspace",
                    action="delete",
                    source=ScopeSource.PLATFORM,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        service_accounts_service,
        "compute_effective_scopes",
        AsyncMock(return_value=frozenset({"org:service_account:create"})),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match=(
            "Cannot assign service account scopes not held by the caller: "
            "workspace:delete"
        ),
    ):
        await service.create_service_account(
            name="Org automation",
            description=None,
            scope_ids=[uuid.uuid4()],
            initial_key_name="Primary",
        )


@pytest.mark.anyio
async def test_update_service_account_rejects_scopes_not_held_by_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_account = SimpleNamespace(
        id=uuid.uuid4(),
        name="Org automation",
        description=None,
        disabled_at=None,
        scopes=[],
    )
    service = OrganizationServiceAccountService(
        cast(AsyncSession, _NoopSession()),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:service_account:update"}),
        ),
    )
    monkeypatch.setattr(
        service, "get_service_account", AsyncMock(return_value=service_account)
    )
    monkeypatch.setattr(
        service,
        "_resolve_scopes",
        AsyncMock(
            return_value=[
                Scope(
                    id=uuid.uuid4(),
                    name="workspace:delete",
                    resource="workspace",
                    action="delete",
                    source=ScopeSource.PLATFORM,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        service_accounts_service,
        "compute_effective_scopes",
        AsyncMock(return_value=frozenset({"org:service_account:update"})),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match=(
            "Cannot assign service account scopes not held by the caller: "
            "workspace:delete"
        ),
    ):
        await service.update_service_account(
            service_account.id,
            name=None,
            description=None,
            description_provided=False,
            scope_ids=[uuid.uuid4()],
        )
