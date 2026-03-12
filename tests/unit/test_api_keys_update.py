from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tracecat.api_keys.service as api_keys_service
from tracecat.api_keys.service import OrganizationApiKeyService
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.db.models import Scope
from tracecat.exceptions import TracecatAuthorizationError


class _NoopSession:
    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any, _attrs: list[str]) -> None:
        return None


@pytest.mark.anyio
async def test_update_key_can_clear_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = SimpleNamespace(
        id=uuid.uuid4(),
        name="Org automation",
        description="to be cleared",
        revoked_at=None,
        scopes=[],
    )
    service = OrganizationApiKeyService(
        cast(AsyncSession, _NoopSession()),
        role=Role(
            type="user",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:api_key:update"}),
        ),
    )
    monkeypatch.setattr(service, "get_key", AsyncMock(return_value=api_key))

    updated = await cast(Any, service).update_key(
        api_key.id,
        name=None,
        description=None,
        description_provided=True,
        scope_ids=None,
    )

    assert updated.description is None


@pytest.mark.anyio
async def test_create_key_rejects_scopes_not_held_by_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OrganizationApiKeyService(
        cast(AsyncSession, _NoopSession()),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:api_key:create"}),
        ),
    )
    monkeypatch.setattr(
        service,
        "_resolve_assignable_scopes",
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
        api_keys_service,
        "compute_effective_scopes",
        AsyncMock(return_value=frozenset({"org:api_key:create"})),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match=(
            "Cannot assign organization API key scopes not held by the caller: "
            "workspace:delete"
        ),
    ):
        await service.create_key(
            name="Org automation",
            description=None,
            scope_ids=[uuid.uuid4()],
        )


@pytest.mark.anyio
async def test_update_key_rejects_scopes_not_held_by_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = SimpleNamespace(
        id=uuid.uuid4(),
        name="Org automation",
        description=None,
        revoked_at=None,
        scopes=[],
    )
    service = OrganizationApiKeyService(
        cast(AsyncSession, _NoopSession()),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            scopes=frozenset({"org:api_key:update"}),
        ),
    )
    monkeypatch.setattr(service, "get_key", AsyncMock(return_value=api_key))
    monkeypatch.setattr(
        service,
        "_resolve_assignable_scopes",
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
        api_keys_service,
        "compute_effective_scopes",
        AsyncMock(return_value=frozenset({"org:api_key:update"})),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match=(
            "Cannot assign organization API key scopes not held by the caller: "
            "workspace:delete"
        ),
    ):
        await cast(Any, service).update_key(
            api_key.id,
            name=None,
            description=None,
            description_provided=False,
            scope_ids=[uuid.uuid4()],
        )
