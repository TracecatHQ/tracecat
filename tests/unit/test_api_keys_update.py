from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api_keys.service import OrganizationApiKeyService
from tracecat.auth.types import Role


class _NoopSession:
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
