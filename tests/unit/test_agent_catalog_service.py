"""Tests for AgentCatalogService."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.db.models import AgentCatalog, AgentCustomProvider, Organization
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_get_catalog_entry_not_found(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    with pytest.raises(TracecatNotFoundError):
        await service.get_catalog_entry(
            org_id=svc_organization.id,
            catalog_id=uuid.uuid4(),
        )


@pytest.mark.anyio
async def test_list_catalog_filters_by_provider(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    for provider in ("openai", "anthropic", "openai"):
        session.add(
            AgentCatalog(
                organization_id=svc_organization.id,
                custom_provider_id=None,
                model_provider=provider,
                model_name=f"{provider}-model-{uuid.uuid4()}",
                model_metadata={},
            )
        )
    await session.commit()

    items, next_cursor = await service.list_catalog(
        org_id=svc_organization.id,
        provider_filter="openai",
        cursor_params=CursorPaginationParams(limit=10),
    )

    assert next_cursor is None
    assert len(items) == 2
    assert all(item.model_provider == "openai" for item in items)


@pytest.mark.anyio
async def test_upsert_catalog_entry_platform_row(session: AsyncSession) -> None:
    service = AgentCatalogService(session=session)
    result = await service.upsert_catalog_entry(
        org_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4.1",
        metadata={"tier": "platform"},
    )

    assert result.id is not None
    assert result.organization_id is None
    assert result.custom_provider_id is None
    assert result.model_name == "gpt-4.1"


@pytest.mark.anyio
async def test_upsert_catalog_entry_refreshes_metadata(session: AsyncSession) -> None:
    service = AgentCatalogService(session=session)
    first = await service.upsert_catalog_entry(
        org_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4o",
        metadata={"max_input_tokens": 128_000},
    )
    second = await service.upsert_catalog_entry(
        org_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4o",
        metadata={"max_input_tokens": 200_000, "mode": "chat"},
    )

    assert first.id == second.id
    assert second.model_metadata == {
        "max_input_tokens": 200_000,
        "mode": "chat",
    }


@pytest.mark.anyio
async def test_upsert_catalog_entry_with_custom_provider(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    provider = AgentCustomProvider(
        organization_id=svc_organization.id,
        display_name="Custom Provider",
        base_url="https://api.example.com",
    )
    session.add(provider)
    await session.commit()

    result = await service.upsert_catalog_entry(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        model_name="custom-model",
    )

    assert result.id is not None
    assert result.custom_provider_id == provider.id
    assert result.organization_id == svc_organization.id


@pytest.mark.anyio
async def test_upsert_discovered_models_inserts_rows(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    provider = AgentCustomProvider(
        organization_id=svc_organization.id,
        display_name="Provider",
        base_url="https://api.example.com",
    )
    session.add(provider)
    await session.commit()

    count = await service.upsert_discovered_models(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        models=[
            {"id": "model-a", "context_window": 8192},
            {"id": "model-b", "context_window": 16384},
        ],
    )

    rows = (
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

    assert count == 2
    assert {row.model_name for row in rows} == {"model-a", "model-b"}
