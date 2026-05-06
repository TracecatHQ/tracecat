from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
async def test_issue_api_key_locks_service_account_before_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_account_id = uuid.uuid4()
    active_key = SimpleNamespace(
        id=uuid.uuid4(),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        revoked_at=None,
        revoked_by=None,
    )
    created_key = SimpleNamespace(id=uuid.uuid4())
    service_account = SimpleNamespace(
        id=service_account_id,
        disabled_at=None,
        api_keys=[active_key],
    )
    refreshed_service_account = SimpleNamespace(
        id=service_account_id,
        disabled_at=None,
        api_keys=[active_key, created_key],
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
    get_service_account = AsyncMock(
        side_effect=[service_account, refreshed_service_account]
    )
    monkeypatch.setattr(service, "get_service_account", get_service_account)
    monkeypatch.setattr(
        service,
        "_create_api_key",
        AsyncMock(return_value=(created_key, "tcok_test_raw")),
    )

    result = await service._issue_api_key(service_account_id, name="Rotated")

    assert result.api_key is created_key
    assert active_key.revoked_at is not None
    assert active_key.revoked_by == service.role.user_id
    get_service_account.assert_any_await(service_account_id, for_update=True)


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
