"""Tests for AgentCatalogService."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.catalog.schemas import (
    AzureOpenAICatalogUpdate,
    BedrockCatalogUpdate,
)
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    Organization,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginationParams

pytestmark = pytest.mark.usefixtures("db")


def _enable(
    *,
    org_id: uuid.UUID,
    catalog_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
) -> AgentModelAccess:
    """Build an AgentModelAccess row (org-level when workspace_id is None)."""
    return AgentModelAccess(
        organization_id=org_id,
        workspace_id=workspace_id,
        catalog_id=catalog_id,
    )


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
async def test_upsert_discovered_models_removes_stale_models(
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

    await service.upsert_discovered_models(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        models=[
            {"id": "model-a"},
            {"id": "model-b"},
            {"id": "model-c"},
        ],
    )

    count = await service.upsert_discovered_models(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        models=[
            {"id": "model-b"},
            {"id": "model-d"},
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
    assert {row.model_name for row in rows} == {"model-b", "model-d"}


@pytest.mark.anyio
async def test_upsert_discovered_models_clears_catalog_when_empty(
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

    await service.upsert_discovered_models(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        models=[{"id": "model-a"}, {"id": "model-b"}],
    )

    count = await service.upsert_discovered_models(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        models=[],
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

    assert count == 0
    assert rows == []


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


# --- BedrockCatalogUpdate validator ---


def test_bedrock_update_use_converse_only_requires_no_ref() -> None:
    """use_converse-only patch is valid; model ref unchanged in DB."""
    update = BedrockCatalogUpdate(model_provider="bedrock", use_converse=True)
    assert update.use_converse is True
    assert update.inference_profile_id is None
    assert update.model_id is None


def test_bedrock_update_with_ref_and_use_converse_is_valid() -> None:
    update = BedrockCatalogUpdate(
        model_provider="bedrock",
        inference_profile_id="us.anthropic.claude-3-haiku",
        use_converse=True,
    )
    assert update.inference_profile_id == "us.anthropic.claude-3-haiku"
    assert update.use_converse is True


def test_bedrock_update_rejects_both_refs_set() -> None:
    with pytest.raises(ValueError, match="at most one"):
        BedrockCatalogUpdate(
            model_provider="bedrock",
            inference_profile_id="us.anthropic.claude-3-haiku",
            model_id="anthropic.claude-3-haiku-20240307-v1:0",
        )


def test_bedrock_update_rejects_both_refs_explicitly_null() -> None:
    """Explicitly sending null for both refs must be rejected."""
    with pytest.raises(ValueError, match="At least one"):
        BedrockCatalogUpdate(
            model_provider="bedrock",
            inference_profile_id=None,
            model_id=None,
        )


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_returns_org_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add(row)
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=row.id))
    await session.commit()

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
    )

    assert resolved == row.id


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_prefers_org_over_platform(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add_all([platform_row, org_row])
    await session.flush()
    # Both enabled at org level; the org-owned row wins.
    session.add_all(
        [
            _enable(org_id=svc_organization.id, catalog_id=platform_row.id),
            _enable(org_id=svc_organization.id, catalog_id=org_row.id),
        ]
    )
    await session.commit()

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
    )

    assert resolved == org_row.id


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_falls_back_to_platform(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4.1",
        model_metadata={},
    )
    session.add(platform_row)
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=platform_row.id))
    await session.commit()

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="openai",
        model_name="gpt-4.1",
    )

    assert resolved == platform_row.id


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_no_match(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="anthropic",
        model_name="does-not-exist",
    )

    assert resolved is None


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_skips_disabled_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """An org row that exists but is not enabled is not selected."""
    service = AgentCatalogService(session=session)
    row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add(row)
    await session.commit()  # no AgentModelAccess row -> not enabled

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
    )

    assert resolved is None


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_prefers_enabled_platform_over_disabled_org(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace,
) -> None:
    """When the org row is disabled but a platform row is enabled, pick platform.

    This is the runtime-rejection case: rewriting to the disabled org row would
    make get_catalog_credentials raise even though an enabled model exists.
    """
    service = AgentCatalogService(session=session)
    org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add_all([org_row, platform_row])
    await session.flush()
    # Only the platform row is enabled (org level); org row is NOT enabled.
    session.add(_enable(org_id=svc_organization.id, catalog_id=platform_row.id))
    await session.commit()

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        workspace_id=svc_workspace.id,
    )

    assert resolved == platform_row.id


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_workspace_override_replaces_org(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace,
) -> None:
    """A workspace override fully replaces the org-level enabled set."""
    service = AgentCatalogService(session=session)
    row_a = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    row_b = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4.1",
        model_metadata={},
    )
    session.add_all([row_a, row_b])
    await session.flush()
    # Org level enables A; the workspace override enables only B.
    session.add(_enable(org_id=svc_organization.id, catalog_id=row_a.id))
    session.add(
        _enable(
            org_id=svc_organization.id,
            catalog_id=row_b.id,
            workspace_id=svc_workspace.id,
        )
    )
    await session.commit()

    # A is org-enabled but the workspace override hides it.
    assert (
        await service.resolve_catalog_id_by_model(
            org_id=svc_organization.id,
            model_provider="anthropic",
            model_name="claude-opus-4-8",
            workspace_id=svc_workspace.id,
        )
        is None
    )
    # B is enabled via the override.
    assert (
        await service.resolve_catalog_id_by_model(
            org_id=svc_organization.id,
            model_provider="openai",
            model_name="gpt-4.1",
            workspace_id=svc_workspace.id,
        )
        == row_b.id
    )


@pytest.mark.anyio
async def test_resolve_catalog_id_by_model_multiple_rows_best_effort(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """Same (provider, name) on multiple custom providers: best-effort pick.

    The information needed to pick the exact row (custom_provider_id) is
    environment-specific and unrecoverable, so we deterministically pick one
    matching row rather than skip the remap.
    """
    service = AgentCatalogService(session=session)
    rows = []
    for name in ("Provider A", "Provider B"):
        p = AgentCustomProvider(
            organization_id=svc_organization.id,
            display_name=name,
            base_url="https://api.example.com",
        )
        session.add(p)
        await session.flush()
        row = AgentCatalog(
            organization_id=svc_organization.id,
            custom_provider_id=p.id,
            model_provider="custom-model-provider",
            model_name="shared-model",
            model_metadata={},
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    for row in rows:
        session.add(_enable(org_id=svc_organization.id, catalog_id=row.id))
    await session.commit()
    candidate_ids = {row.id for row in rows}

    resolved = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="custom-model-provider",
        model_name="shared-model",
    )

    # Picks one of the matching org rows (deterministically), never None.
    assert resolved in candidate_ids
    # Stable across calls.
    again = await service.resolve_catalog_id_by_model(
        org_id=svc_organization.id,
        model_provider="custom-model-provider",
        model_name="shared-model",
    )
    assert again == resolved


@pytest.mark.anyio
async def test_is_catalog_id_enabled_true_for_enabled_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """An org row that is enabled at org level reports as enabled."""
    service = AgentCatalogService(session=session)
    row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add(row)
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=row.id))
    await session.commit()

    assert (
        await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=row.id,
        )
        is True
    )


@pytest.mark.anyio
async def test_is_catalog_id_enabled_false_for_disabled_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """A visible row with no access row reports as not enabled."""
    service = AgentCatalogService(session=session)
    row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add(row)
    await session.commit()

    assert (
        await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=row.id,
        )
        is False
    )


@pytest.mark.anyio
async def test_is_catalog_id_enabled_false_for_foreign_org_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """A row owned by another org is not visible, even if enabled there."""
    service = AgentCatalogService(session=session)
    other_org_id = uuid.uuid4()
    other_org = Organization(
        id=other_org_id,
        name="Other Org",
        slug=f"other-org-{other_org_id.hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.flush()
    row = AgentCatalog(
        organization_id=other_org.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    session.add(row)
    await session.flush()
    session.add(_enable(org_id=other_org.id, catalog_id=row.id))
    await session.commit()

    assert (
        await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=row.id,
        )
        is False
    )


@pytest.mark.anyio
async def test_is_catalog_id_enabled_respects_workspace_override(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
) -> None:
    """A workspace override replaces the org-level set for the enabled check."""
    service = AgentCatalogService(session=session)
    org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="anthropic",
        model_name="claude-opus-4-8",
        model_metadata={},
    )
    ws_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="openai",
        model_name="gpt-4.1",
        model_metadata={},
    )
    session.add_all([org_row, ws_row])
    await session.flush()
    # Org level enables org_row; the workspace override enables only ws_row.
    session.add(_enable(org_id=svc_organization.id, catalog_id=org_row.id))
    session.add(
        _enable(
            org_id=svc_organization.id,
            catalog_id=ws_row.id,
            workspace_id=svc_workspace.id,
        )
    )
    await session.commit()

    # org_row is org-enabled but the workspace override hides it.
    assert (
        await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=org_row.id,
            workspace_id=svc_workspace.id,
        )
        is False
    )
    # ws_row is enabled via the override.
    assert (
        await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=ws_row.id,
            workspace_id=svc_workspace.id,
        )
        is True
    )


@pytest.mark.anyio
async def test_enabled_catalog_ids_returns_visible_enabled_subset(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    other_org_id = uuid.uuid4()
    other_org = Organization(
        id=other_org_id,
        name="Other Batch Org",
        slug=f"other-batch-org-{other_org_id.hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.flush()
    enabled_org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="batch-provider",
        model_name="enabled-org-model",
        model_metadata={},
    )
    enabled_platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider="batch-provider",
        model_name="enabled-platform-model",
        model_metadata={},
    )
    disabled_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="batch-provider",
        model_name="disabled-model",
        model_metadata={},
    )
    other_org_row = AgentCatalog(
        organization_id=other_org.id,
        custom_provider_id=None,
        model_provider="batch-provider",
        model_name="other-org-model",
        model_metadata={},
    )
    session.add_all(
        [
            enabled_org_row,
            enabled_platform_row,
            disabled_row,
            other_org_row,
        ]
    )
    await session.flush()
    session.add_all(
        [
            _enable(org_id=svc_organization.id, catalog_id=enabled_org_row.id),
            _enable(org_id=svc_organization.id, catalog_id=enabled_platform_row.id),
            # Access alone must not expose another organization's catalog row.
            _enable(org_id=svc_organization.id, catalog_id=other_org_row.id),
        ]
    )
    await session.commit()
    unknown_id = uuid.uuid4()
    catalog_ids = {
        enabled_org_row.id,
        enabled_platform_row.id,
        disabled_row.id,
        other_org_row.id,
        unknown_id,
    }

    enabled = await service.enabled_catalog_ids(
        org_id=svc_organization.id,
        catalog_ids=catalog_ids,
    )

    assert enabled == {enabled_org_row.id, enabled_platform_row.id}
    for catalog_id in catalog_ids:
        assert (catalog_id in enabled) is await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=catalog_id,
        )


@pytest.mark.anyio
async def test_enabled_catalog_ids_respects_workspace_override(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
) -> None:
    service = AgentCatalogService(session=session)
    org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="batch-provider",
        model_name="org-level-model",
        model_metadata={},
    )
    workspace_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider="batch-provider",
        model_name="workspace-level-model",
        model_metadata={},
    )
    session.add_all([org_row, workspace_row])
    await session.flush()
    session.add_all(
        [
            _enable(org_id=svc_organization.id, catalog_id=org_row.id),
            _enable(
                org_id=svc_organization.id,
                catalog_id=workspace_row.id,
                workspace_id=svc_workspace.id,
            ),
        ]
    )
    await session.commit()
    catalog_ids = {org_row.id, workspace_row.id}

    enabled = await service.enabled_catalog_ids(
        org_id=svc_organization.id,
        workspace_id=svc_workspace.id,
        catalog_ids=catalog_ids,
    )

    assert enabled == {workspace_row.id}
    for catalog_id in catalog_ids:
        assert (catalog_id in enabled) is await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            workspace_id=svc_workspace.id,
            catalog_id=catalog_id,
        )


@pytest.mark.anyio
async def test_resolve_catalog_ids_by_models_matches_single_item_selection(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    preferred_key = ("batch-provider", "org-preferred-model")
    duplicate_key = ("batch-custom-provider", "duplicate-model")
    fallback_key = ("batch-provider", "platform-fallback-model")
    unknown_key = ("batch-provider", "unknown-model")

    preferred_platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider=preferred_key[0],
        model_name=preferred_key[1],
        model_metadata={},
    )
    preferred_org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider=preferred_key[0],
        model_name=preferred_key[1],
        model_metadata={},
    )
    disabled_org_row = AgentCatalog(
        organization_id=svc_organization.id,
        custom_provider_id=None,
        model_provider=fallback_key[0],
        model_name=fallback_key[1],
        model_metadata={},
    )
    fallback_platform_row = AgentCatalog(
        organization_id=None,
        custom_provider_id=None,
        model_provider=fallback_key[0],
        model_name=fallback_key[1],
        model_metadata={},
    )
    duplicate_rows: list[AgentCatalog] = []
    for display_name in ("Batch Provider A", "Batch Provider B"):
        provider = AgentCustomProvider(
            organization_id=svc_organization.id,
            display_name=display_name,
            base_url="https://api.example.com",
        )
        session.add(provider)
        await session.flush()
        duplicate_rows.append(
            AgentCatalog(
                organization_id=svc_organization.id,
                custom_provider_id=provider.id,
                model_provider=duplicate_key[0],
                model_name=duplicate_key[1],
                model_metadata={},
            )
        )
    session.add_all(
        [
            preferred_platform_row,
            preferred_org_row,
            disabled_org_row,
            fallback_platform_row,
            *duplicate_rows,
        ]
    )
    await session.flush()
    enabled_rows = [
        preferred_platform_row,
        preferred_org_row,
        fallback_platform_row,
        *duplicate_rows,
    ]
    session.add_all(
        [_enable(org_id=svc_organization.id, catalog_id=row.id) for row in enabled_rows]
    )
    await session.commit()
    models = {preferred_key, duplicate_key, fallback_key, unknown_key}
    expected_duplicate_id = min(row.id for row in duplicate_rows)

    resolved = await service.resolve_catalog_ids_by_models(
        org_id=svc_organization.id,
        models=models,
    )

    assert resolved == {
        preferred_key: preferred_org_row.id,
        duplicate_key: expected_duplicate_id,
        fallback_key: fallback_platform_row.id,
    }
    for model_provider, model_name in models:
        assert resolved.get(
            (model_provider, model_name)
        ) == await service.resolve_catalog_id_by_model(
            org_id=svc_organization.id,
            model_provider=model_provider,
            model_name=model_name,
        )
