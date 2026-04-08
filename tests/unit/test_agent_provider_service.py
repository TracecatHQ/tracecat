"""Tests for AgentCustomProviderService."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.provider import service as provider_service_module
from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.service import AgentCustomProviderService
from tracecat.auth.types import Role
from tracecat.db.models import AgentCatalog, Organization
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def set_db_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider_service_module.config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )


def _role(org: Organization) -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=org.id,
        service_id="tracecat-api",
    )


@pytest.mark.anyio
async def test_create_provider_minimal(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    result = await service.create_provider(
        AgentCustomProviderCreate(display_name="Test Provider")
    )

    assert result.id is not None
    assert result.display_name == "Test Provider"
    assert result.discovery_status == "never"


@pytest.mark.anyio
async def test_get_provider_not_found(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    with pytest.raises(TracecatNotFoundError):
        await service.get_provider(uuid.uuid4())


@pytest.mark.anyio
async def test_list_providers_paginated(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    for i in range(3):
        await service.create_provider(
            AgentCustomProviderCreate(display_name=f"Provider {i}")
        )

    first_page = await service.list_providers(CursorPaginationParams(limit=2))
    second_page = await service.list_providers(
        CursorPaginationParams(limit=2, cursor=first_page.next_cursor)
    )

    assert len(first_page.items) == 2
    assert first_page.has_more is True
    assert len(second_page.items) == 1


@pytest.mark.anyio
async def test_update_provider(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Original")
    )

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(
            display_name="Updated",
            base_url="https://api.example.com",
        ),
    )

    assert updated.display_name == "Updated"
    assert updated.base_url == "https://api.example.com"


@pytest.mark.anyio
async def test_delete_provider(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Delete me")
    )

    await service.delete_provider(created.id)

    with pytest.raises(TracecatNotFoundError):
        await service.get_provider(created.id)


@pytest.mark.anyio
async def test_refresh_provider_catalog_upserts_models(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    provider = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="Refreshable",
            base_url="https://api.example.com",
        )
    )

    with patch.object(
        service,
        "_discover_models",
        return_value=[{"id": "model-a"}, {"id": "model-b"}],
    ):
        await service.refresh_provider_catalog(provider.id)

    catalog_rows = (
        (
            await session.execute(
                select(AgentCatalog).where(
                    AgentCatalog.custom_provider_id == provider.id
                )
            )
        )
        .scalars()
        .all()
    )

    assert {row.model_name for row in catalog_rows} == {"model-a", "model-b"}


@pytest.mark.anyio
async def test_validate_provider_success(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))

    class _Response:
        status_code = 200

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str]):
            assert url == "https://api.example.com/models"
            assert headers == {"Authorization": "secret"}
            return _Response()

    with patch.object(
        provider_service_module.httpx, "AsyncClient", return_value=_Client()
    ):
        result = await service.validate_provider(
            base_url="https://api.example.com",
            api_key="secret",
            api_key_header="Authorization",
        )

    assert result is True
