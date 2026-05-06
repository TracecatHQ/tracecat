"""Tests for AgentCatalogService."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.catalog.schemas import AzureOpenAICatalogUpdate
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import AgentCatalog, AgentCustomProvider, Organization
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginationParams

pytestmark = pytest.mark.usefixtures("db")


def _user_role(organization_id: uuid.UUID) -> Role:
    return Role(
        type="user",
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )


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
async def test_list_platform_catalog_excludes_org_rows(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    session.add_all(
        [
            AgentCatalog(
                organization_id=None,
                custom_provider_id=None,
                model_provider="openai",
                model_name="platform-model",
                model_metadata={},
            ),
            AgentCatalog(
                organization_id=svc_organization.id,
                custom_provider_id=None,
                model_provider="openai",
                model_name="org-model",
                model_metadata={},
            ),
        ]
    )
    await session.commit()

    items, next_cursor = await service.list_platform_catalog(
        cursor_params=CursorPaginationParams(limit=10),
    )

    assert next_cursor is None
    assert {item.model_name for item in items} == {"platform-model"}


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


@pytest.mark.anyio
async def test_get_catalog_entry_returns_platform_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """Platform rows (org_id NULL) are visible via the caller's org lookup."""
    service = AgentCatalogService(session=session)
    platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4o",
        model_metadata={},
    )
    session.add(platform_row)
    await session.commit()

    fetched = await service.get_catalog_entry(
        org_id=svc_organization.id,
        catalog_id=platform_row.id,
    )
    assert fetched.id == platform_row.id
    assert fetched.organization_id is None


@pytest.mark.anyio
async def test_get_catalog_entry_rejects_other_org(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """Rows owned by a different org surface as not found."""
    other_org_id = uuid.uuid4()
    other_org = Organization(
        id=other_org_id,
        name="Other Org",
        slug=f"other-org-{other_org_id.hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.commit()
    other_row = AgentCatalog(
        organization_id=other_org.id,
        custom_provider_id=None,
        model_provider="bedrock",
        model_name="claude-cross-org",
        model_metadata={},
    )
    session.add(other_row)
    await session.commit()

    service = AgentCatalogService(session=session)
    with pytest.raises(TracecatNotFoundError):
        await service.get_catalog_entry(
            org_id=svc_organization.id,
            catalog_id=other_row.id,
        )


@pytest.mark.anyio
async def test_create_catalog_entry_persists_metadata(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="bedrock",
            model_name="claude-sonnet-4",
            metadata={
                "inference_profile_id": "us.anthropic.claude-sonnet-4",
                "max_input_tokens": 200_000,
            },
        )
    finally:
        ctx_role.reset(token)

    assert row.organization_id == svc_organization.id
    assert row.custom_provider_id is None
    assert row.model_metadata == {
        "inference_profile_id": "us.anthropic.claude-sonnet-4",
        "max_input_tokens": 200_000,
    }


@pytest.mark.anyio
async def test_create_catalog_entry_rejects_duplicate(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="azure_openai",
            model_name="gpt-4o-deploy",
            metadata={"deployment_name": "gpt-4o"},
        )
        with pytest.raises(TracecatValidationError):
            await service.create_catalog_entry(
                org_id=svc_organization.id,
                model_provider="azure_openai",
                model_name="gpt-4o-deploy",
                metadata={"deployment_name": "gpt-4o"},
            )
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_update_catalog_entry_replaces_metadata(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    role = _user_role(svc_organization.id)
    token = ctx_role.set(role)
    try:
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="vertex_ai",
            model_name="gemini-2-flash",
            metadata={"vertex_model": "gemini-2.5-flash"},
        )
        updated = await service.update_catalog_entry(
            row,
            org_id=svc_organization.id,
            expected_provider="vertex_ai",
            metadata={"vertex_model": "gemini-3-pro", "max_input_tokens": 1_000_000},
        )
    finally:
        ctx_role.reset(token)

    assert updated.model_metadata == {
        "vertex_model": "gemini-3-pro",
        "max_input_tokens": 1_000_000,
    }


def test_catalog_update_dump_excludes_unset_optional_metadata() -> None:
    params = AzureOpenAICatalogUpdate(
        model_provider="azure_openai",
        deployment_name="gpt-4o-updated",
    )

    metadata = params.model_dump(exclude={"model_provider"}, exclude_unset=True)

    assert metadata == {"deployment_name": "gpt-4o-updated"}


@pytest.mark.anyio
async def test_update_catalog_entry_preserves_unspecified_metadata(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="azure_openai",
            model_name="gpt-4o-deploy",
            metadata={
                "deployment_name": "gpt-4o",
                "display_name": "Customer GPT-4o",
                "max_input_tokens": 128_000,
            },
        )
        updated = await service.update_catalog_entry(
            row,
            org_id=svc_organization.id,
            expected_provider="azure_openai",
            metadata={"deployment_name": "gpt-4o-updated"},
        )
    finally:
        ctx_role.reset(token)

    assert updated.model_metadata == {
        "deployment_name": "gpt-4o-updated",
        "display_name": "Customer GPT-4o",
        "max_input_tokens": 128_000,
    }


@pytest.mark.anyio
async def test_update_catalog_entry_rejects_provider_mismatch(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="bedrock",
            model_name="mismatch-model",
            metadata={"model_id": "anthropic.claude-legacy"},
        )
        with pytest.raises(TracecatValidationError):
            await service.update_catalog_entry(
                row,
                org_id=svc_organization.id,
                expected_provider="azure_openai",
                metadata={"deployment_name": "nope"},
            )
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_update_catalog_entry_rejects_platform_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-platform",
        model_metadata={},
    )
    session.add(platform_row)
    await session.commit()

    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        with pytest.raises(TracecatNotFoundError):
            await service.update_catalog_entry(
                platform_row,
                org_id=svc_organization.id,
                expected_provider="openai",
                metadata={},
            )
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_delete_catalog_entry_removes_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="azure_ai",
            model_name="to-delete",
            metadata={"azure_ai_model_name": "claude-4"},
        )
        row_id = row.id
        await service.delete_catalog_entry(row, org_id=svc_organization.id)
    finally:
        ctx_role.reset(token)

    assert (
        await session.execute(select(AgentCatalog).where(AgentCatalog.id == row_id))
    ).scalar_one_or_none() is None


@pytest.mark.anyio
async def test_delete_catalog_entry_rejects_platform_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-platform-delete",
        model_metadata={},
    )
    session.add(platform_row)
    await session.commit()

    token = ctx_role.set(_user_role(svc_organization.id))
    try:
        with pytest.raises(TracecatNotFoundError):
            await service.delete_catalog_entry(
                platform_row,
                org_id=svc_organization.id,
            )
    finally:
        ctx_role.reset(token)
