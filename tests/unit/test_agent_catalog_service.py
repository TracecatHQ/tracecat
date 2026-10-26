"""Tests for AgentCatalogService."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.bedrock import (
    BedrockDiscoveredModel,
    BedrockSubscription,
    BedrockSubscriptionStatus,
)
from tracecat.agent.catalog.schemas import (
    BedrockCatalogUpdate,
)
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.catalog.types import ModelKey, ModelLifecycle, ModelReplacement
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    Organization,
    OrganizationSetting,
)
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)
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


@contextmanager
def _acting_as(organization_id: uuid.UUID) -> Iterator[None]:
    token = ctx_role.set(_user_role(organization_id))
    try:
        yield
    finally:
        ctx_role.reset(token)


def _catalog(
    org_id: uuid.UUID | None,
    model_provider: str = "anthropic",
    model_name: str = "claude-opus-4-8",
    *,
    custom_provider_id: uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> AgentCatalog:
    return AgentCatalog(
        organization_id=org_id,
        custom_provider_id=custom_provider_id,
        model_provider=model_provider,
        model_name=model_name,
        model_metadata=metadata or {},
    )


async def _other_org(session: AsyncSession) -> Organization:
    other_org_id = uuid.uuid4()
    other_org = Organization(
        id=other_org_id,
        name="Other Org",
        slug=f"other-org-{other_org_id.hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.flush()
    return other_org


async def _custom_provider(
    session: AsyncSession,
    org_id: uuid.UUID,
    display_name: str = "Provider",
    base_url: str = "https://api.example.com",
) -> AgentCustomProvider:
    provider = AgentCustomProvider(
        organization_id=org_id,
        display_name=display_name,
        base_url=base_url,
    )
    session.add(provider)
    await session.flush()
    return provider


async def _provider_model_names(
    session: AsyncSession, custom_provider_id: uuid.UUID
) -> set[str]:
    result = await session.execute(
        select(AgentCatalog.model_name).where(
            AgentCatalog.custom_provider_id == custom_provider_id
        )
    )
    return set(result.scalars().all())


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
            _catalog(None, "openai", "platform-model"),
            _catalog(svc_organization.id, "openai", "org-model"),
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
    provider = await _custom_provider(session, svc_organization.id, "Custom Provider")

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
    provider = await _custom_provider(session, svc_organization.id)

    count = await service.upsert_discovered_models(
        org_id=svc_organization.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        models=[
            {"id": "model-a", "context_window": 8192},
            {"id": "model-b", "context_window": 16384},
        ],
    )

    model_names = await _provider_model_names(session, provider.id)

    assert count == 2
    assert model_names == {"model-a", "model-b"}


@pytest.mark.anyio
async def test_upsert_discovered_models_removes_stale_models(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    provider = await _custom_provider(session, svc_organization.id)

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

    model_names = await _provider_model_names(session, provider.id)

    assert count == 2
    assert model_names == {"model-b", "model-d"}


@pytest.mark.anyio
async def test_upsert_discovered_models_clears_catalog_when_empty(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    provider = await _custom_provider(session, svc_organization.id)

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

    model_names = await _provider_model_names(session, provider.id)

    assert count == 0
    assert model_names == set()


@pytest.mark.anyio
async def test_get_catalog_entry_returns_platform_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """Platform rows (org_id NULL) are visible via the caller's org lookup."""
    service = AgentCatalogService(session=session)
    platform_row = _catalog(None, "openai", "gpt-4o")
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
    other_org = await _other_org(session)
    other_row = _catalog(other_org.id, "bedrock", "claude-cross-org")
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
    with _acting_as(svc_organization.id):
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="bedrock",
            model_name="claude-sonnet-4",
            metadata={
                "inference_profile_id": "us.anthropic.claude-sonnet-4",
                "max_input_tokens": 200_000,
            },
        )

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
    with _acting_as(svc_organization.id):
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


@pytest.mark.anyio
async def test_update_catalog_entry_replaces_metadata(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    with _acting_as(svc_organization.id):
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

    assert updated.model_metadata == {
        "vertex_model": "gemini-3-pro",
        "max_input_tokens": 1_000_000,
    }


@pytest.mark.anyio
async def test_update_catalog_entry_preserves_unspecified_metadata(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    with _acting_as(svc_organization.id):
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
    with _acting_as(svc_organization.id):
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


@pytest.mark.anyio
async def test_update_catalog_entry_rejects_platform_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    platform_row = _catalog(None, "openai", "gpt-platform")
    session.add(platform_row)
    await session.commit()

    with _acting_as(svc_organization.id):
        with pytest.raises(TracecatNotFoundError):
            await service.update_catalog_entry(
                platform_row,
                org_id=svc_organization.id,
                expected_provider="openai",
                metadata={},
            )


@pytest.mark.anyio
async def test_delete_catalog_entry_removes_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    with _acting_as(svc_organization.id):
        row = await service.create_catalog_entry(
            org_id=svc_organization.id,
            model_provider="azure_ai",
            model_name="to-delete",
            metadata={"azure_ai_model_name": "claude-4"},
        )
        row_id = row.id
        await service.delete_catalog_entry(row, org_id=svc_organization.id)

    assert (
        await session.execute(select(AgentCatalog).where(AgentCatalog.id == row_id))
    ).scalar_one_or_none() is None


@pytest.mark.anyio
async def test_delete_catalog_entry_rejects_platform_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    platform_row = _catalog(None, "openai", "gpt-platform-delete")
    session.add(platform_row)
    await session.commit()

    with _acting_as(svc_organization.id):
        with pytest.raises(TracecatNotFoundError):
            await service.delete_catalog_entry(
                platform_row,
                org_id=svc_organization.id,
            )


# --- BedrockCatalogUpdate validator ---


def test_bedrock_update_use_converse_only_requires_no_ref() -> None:
    """use_converse-only patch is valid; model ref unchanged in DB."""
    update = BedrockCatalogUpdate(model_provider="bedrock", use_converse=True)
    assert update.use_converse is True
    assert update.inference_profile_id is None
    assert update.model_id is None


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
    row = _catalog(svc_organization.id)
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
    platform_row = _catalog(None)
    org_row = _catalog(svc_organization.id)
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
    platform_row = _catalog(None, "openai", "gpt-4.1")
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
async def test_resolve_catalog_id_by_model_skips_disabled_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """An org row that exists but is not enabled is not selected."""
    service = AgentCatalogService(session=session)
    row = _catalog(svc_organization.id)
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
    org_row = _catalog(svc_organization.id)
    platform_row = _catalog(None)
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
async def test_resolve_catalog_id_by_model_workspace_subset_narrows_org(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace,
) -> None:
    """A workspace subset narrows the org-level enabled set."""
    service = AgentCatalogService(session=session)
    row_a = _catalog(svc_organization.id)
    row_b = _catalog(svc_organization.id, "openai", "gpt-4.1")
    session.add_all([row_a, row_b])
    await session.flush()
    # Org level enables both; the workspace subset keeps only B.
    session.add(_enable(org_id=svc_organization.id, catalog_id=row_a.id))
    session.add(_enable(org_id=svc_organization.id, catalog_id=row_b.id))
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
        p = await _custom_provider(session, svc_organization.id, name)
        row = _catalog(
            svc_organization.id,
            "custom-model-provider",
            "shared-model",
            custom_provider_id=p.id,
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
async def test_catalog_candidates_by_models_preserves_duplicate_custom_providers(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """Interactive correlation receives every enabled provider with safe labels."""
    service = AgentCatalogService(session=session)
    model_key = ModelKey("custom-model-provider", "shared-model-for-choice")
    enabled_rows: list[AgentCatalog] = []
    for display_name, base_url in (
        (
            "Provider East",
            "https://user:secret@east.models.example.com/v1",
        ),
        ("Provider West", "https://west.models.example.com/v1"),
    ):
        provider = await _custom_provider(
            session, svc_organization.id, display_name, base_url
        )
        row = _catalog(
            svc_organization.id,
            model_key.model_provider,
            model_key.model_name,
            custom_provider_id=provider.id,
            metadata={"display_name": "Shared model"},
        )
        session.add(row)
        enabled_rows.append(row)

    disabled_provider = await _custom_provider(
        session,
        svc_organization.id,
        "Disabled provider",
        "https://disabled.models.example.com",
    )
    session.add(
        _catalog(
            svc_organization.id,
            model_key.model_provider,
            model_key.model_name,
            custom_provider_id=disabled_provider.id,
        )
    )
    await session.flush()
    session.add_all(
        [_enable(org_id=svc_organization.id, catalog_id=row.id) for row in enabled_rows]
    )
    await session.commit()

    candidates = await service.catalog_candidates_by_models(
        org_id=svc_organization.id,
        models={model_key},
    )

    by_provider = {
        candidate.provider_name: candidate for candidate in candidates[model_key]
    }
    assert set(by_provider) == {"Provider East", "Provider West"}
    assert by_provider["Provider East"].endpoint_hostname == "east.models.example.com"
    assert by_provider["Provider West"].endpoint_hostname == "west.models.example.com"
    assert by_provider["Provider East"].model_display_name == "Shared model"
    assert all(
        candidate.origin == "custom_provider" for candidate in by_provider.values()
    )


@pytest.mark.anyio
async def test_is_catalog_id_enabled_false_for_foreign_org_row(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    """A row owned by another org is not visible, even if enabled there."""
    service = AgentCatalogService(session=session)
    other_org = await _other_org(session)
    row = _catalog(other_org.id)
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
async def test_enabled_catalog_models_returns_visible_enabled_subset(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    other_org = await _other_org(session)
    enabled_org_row = _catalog(
        svc_organization.id, "batch-provider", "enabled-org-model"
    )
    enabled_platform_row = _catalog(None, "batch-provider", "enabled-platform-model")
    disabled_row = _catalog(svc_organization.id, "batch-provider", "disabled-model")
    other_org_row = _catalog(other_org.id, "batch-provider", "other-org-model")
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

    enabled_models = await service.enabled_catalog_models(
        org_id=svc_organization.id,
        catalog_ids=catalog_ids,
    )

    assert set(enabled_models) == {enabled_org_row.id, enabled_platform_row.id}
    for catalog_id in catalog_ids:
        assert (catalog_id in enabled_models) is await service.is_catalog_id_enabled(
            org_id=svc_organization.id,
            catalog_id=catalog_id,
        )


@pytest.mark.anyio
async def test_enabled_catalog_models_returns_batch_model_identities(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCatalogService(session=session)
    enabled_row = _catalog(svc_organization.id, "batch-provider", "enabled-model")
    disabled_row = _catalog(svc_organization.id, "batch-provider", "disabled-model")
    session.add_all([enabled_row, disabled_row])
    await session.flush()
    session.add(
        _enable(
            org_id=svc_organization.id,
            catalog_id=enabled_row.id,
        )
    )
    await session.commit()

    enabled_models = await service.enabled_catalog_models(
        org_id=svc_organization.id,
        catalog_ids={enabled_row.id, disabled_row.id, uuid.uuid4()},
    )

    assert enabled_models == {
        enabled_row.id: ModelKey("batch-provider", "enabled-model")
    }


def _bedrock_subscription(status: BedrockSubscriptionStatus) -> BedrockSubscription:
    return {"status": status, "checked_at": "2026-01-01T00:00:00+00:00"}


async def _bedrock_rows(
    session: AsyncSession, org_id: uuid.UUID
) -> dict[str, AgentCatalog]:
    result = await session.execute(
        select(AgentCatalog).where(
            AgentCatalog.organization_id == org_id,
            AgentCatalog.model_provider == "bedrock",
        )
    )
    return {row.model_name: row for row in result.scalars().all()}


@pytest.mark.anyio
async def test_sync_bedrock_models_merges_discovered_and_manual_rows(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    org_id = svc_organization.id
    session.add_all(
        [
            AgentCatalog(
                organization_id=org_id,
                model_provider="bedrock",
                model_name="manual-profile",
                model_metadata={"inference_profile_id": "us.vendor.a-v1"},
            ),
            AgentCatalog(
                organization_id=org_id,
                model_provider="bedrock",
                model_name="manual-unlisted",
                model_metadata={"model_id": "vendor.private-v1"},
            ),
            AgentCatalog(
                organization_id=org_id,
                model_provider="bedrock",
                model_name="vendor.removed-v1",
                model_metadata={
                    "discovery_source": "bedrock",
                    "model_id": "vendor.removed-v1",
                },
            ),
        ]
    )
    await session.commit()
    service = AgentCatalogService(session=session)

    count = await service.sync_bedrock_models(
        org_id=org_id,
        models=[
            BedrockDiscoveredModel(
                model_name="us.vendor.a-v1",
                display_name="Profile A",
                foundation_model_id="vendor.a-v1",
                inference_profile_id="us.vendor.a-v1",
            ),
            BedrockDiscoveredModel(
                model_name="vendor.b-v1",
                display_name="Model B",
                foundation_model_id="vendor.b-v1",
                model_id="vendor.b-v1",
            ),
        ],
        subscriptions={
            "vendor.a-v1": _bedrock_subscription("subscribed"),
            "vendor.b-v1": _bedrock_subscription("not_subscribed"),
            "vendor.private-v1": _bedrock_subscription("pending"),
        },
    )

    rows = await _bedrock_rows(session, org_id)
    assert count == 2
    assert set(rows) == {
        "manual-profile",
        "manual-unlisted",
        "us.vendor.a-v1",
        "vendor.b-v1",
    }
    manual_profile = rows["manual-profile"].model_metadata or {}
    assert manual_profile["foundation_model_id"] == "vendor.a-v1"
    assert manual_profile["bedrock_subscription"]["status"] == "subscribed"
    assert "discovery_source" not in manual_profile
    manual_unlisted = rows["manual-unlisted"].model_metadata or {}
    assert manual_unlisted["bedrock_subscription"]["status"] == "pending"
    discovered = rows["vendor.b-v1"].model_metadata or {}
    assert discovered["discovery_source"] == "bedrock"
    assert discovered["model_id"] == "vendor.b-v1"
    assert discovered["display_name"] == "Model B"
    assert discovered["bedrock_subscription"]["status"] == "not_subscribed"


@pytest.mark.anyio
async def test_set_bedrock_subscription_updates_rows_for_foundation_model(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    org_id = svc_organization.id
    target = AgentCatalog(
        organization_id=org_id,
        model_provider="bedrock",
        model_name="profile",
        model_metadata={"inference_profile_id": "us.vendor.a-v1"},
    )
    sibling = AgentCatalog(
        organization_id=org_id,
        model_provider="bedrock",
        model_name="vendor.a-v1",
        model_metadata={"model_id": "vendor.a-v1"},
    )
    other = AgentCatalog(
        organization_id=org_id,
        model_provider="bedrock",
        model_name="vendor.b-v1",
        model_metadata={"model_id": "vendor.b-v1"},
    )
    session.add_all([target, sibling, other])
    await session.commit()

    await AgentCatalogService(session=session).set_bedrock_subscription(
        org_id=org_id,
        catalog_id=target.id,
        foundation_model_id="vendor.a-v1",
        subscription=_bedrock_subscription("pending"),
    )

    rows = await _bedrock_rows(session, org_id)
    target_metadata = rows["profile"].model_metadata or {}
    assert target_metadata["foundation_model_id"] == "vendor.a-v1"
    assert target_metadata["bedrock_subscription"]["status"] == "pending"
    sibling_metadata = rows["vendor.a-v1"].model_metadata or {}
    assert sibling_metadata["bedrock_subscription"]["status"] == "pending"
    assert "bedrock_subscription" not in (rows["vendor.b-v1"].model_metadata or {})


@pytest.mark.anyio
async def test_update_bedrock_catalog_entry_clears_stale_subscription_on_retarget(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    org_id = svc_organization.id
    row = AgentCatalog(
        organization_id=org_id,
        model_provider="bedrock",
        model_name="manual",
        model_metadata={
            "model_id": "vendor.a-v1",
            "foundation_model_id": "vendor.a-v1",
            "bedrock_subscription": _bedrock_subscription("subscribed"),
        },
    )
    session.add(row)
    await session.commit()
    with _acting_as(org_id):
        updated = await AgentCatalogService(session=session).update_catalog_entry(
            row,
            org_id=org_id,
            expected_provider="bedrock",
            metadata={"model_id": "vendor.b-v1"},
        )

    assert updated.model_metadata == {"model_id": "vendor.b-v1"}


def _retired(replacement: str | None) -> dict[str, object]:
    lifecycle: dict[str, object] = {"retires_on": "2000-01-01"}
    if replacement is not None:
        lifecycle["replacement"] = replacement
    return {"lifecycle": lifecycle}


def _auto_migrate(
    organization_id: uuid.UUID, *, enabled: bool = True
) -> OrganizationSetting:
    return OrganizationSetting(
        organization_id=organization_id,
        key="agent_auto_migrate_retired_models",
        value=orjson.dumps(enabled),
        value_type="json",
        is_encrypted=False,
    )


@pytest.mark.anyio
async def test_resolve_model_replacement_moves_retired_model_to_enabled_replacement(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    retired = _catalog(
        None, "openai", "gpt-5.1-codex-max", metadata=_retired("gpt-5.6-sol")
    )
    replacement = _catalog(None, "openai", "gpt-5.6-sol")
    session.add_all([retired, replacement])
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=replacement.id))
    session.add(_auto_migrate(svc_organization.id))
    await session.commit()
    service = AgentCatalogService(session=session)

    resolved = await service.resolve_model_replacement(
        role=_user_role(svc_organization.id),
        model_provider="openai",
        model_name="gpt-5.1-codex-max",
        catalog_id=retired.id,
    )
    legacy = await service.resolve_model_replacement(
        role=_user_role(svc_organization.id),
        model_provider="openai",
        model_name="gpt-5.1-codex-max",
        catalog_id=None,
    )

    assert resolved == ModelReplacement(
        model_provider="openai", model_name="gpt-5.6-sol", catalog_id=replacement.id
    )
    # Configs without a catalog row keep resolving credentials by provider.
    assert legacy == ModelReplacement(
        model_provider="openai", model_name="gpt-5.6-sol", catalog_id=None
    )


@pytest.mark.anyio
async def test_resolve_model_replacement_leaves_current_and_deprecated_models(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    deprecated = _catalog(
        None,
        "anthropic",
        "claude-sonnet-4-5",
        metadata={
            "lifecycle": {
                "retires_on": "2999-01-01",
                "replacement": "claude-sonnet-5-5",
            }
        },
    )
    session.add_all([deprecated, _catalog(None, "anthropic", "claude-sonnet-5-5")])
    await session.commit()
    service = AgentCatalogService(session=session)

    for model_name in ("claude-sonnet-4-5", "claude-sonnet-5-5", "not-in-catalog"):
        assert (
            await service.resolve_model_replacement(
                role=_user_role(svc_organization.id),
                model_provider="anthropic",
                model_name=model_name,
                catalog_id=None,
            )
            is None
        )


@pytest.mark.anyio
async def test_resolve_model_replacement_ignores_org_rows_lifecycle(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    org_row = _catalog(
        svc_organization.id,
        "openai",
        "gpt-5.1-codex-max",
        metadata=_retired(None),
    )
    session.add(org_row)
    await session.commit()
    service = AgentCatalogService(session=session)

    for catalog_id in (org_row.id, None):
        assert (
            await service.resolve_model_replacement(
                role=_user_role(svc_organization.id),
                model_provider="openai",
                model_name="gpt-5.1-codex-max",
                catalog_id=catalog_id,
            )
            is None
        )


@pytest.mark.anyio
@pytest.mark.parametrize("replacement", [None, "gpt-5.6-sol"])
async def test_resolve_model_replacement_keeps_retired_model_without_usable_replacement(
    session: AsyncSession,
    svc_organization: Organization,
    replacement: str | None,
) -> None:
    retired = _catalog(
        None, "openai", "gpt-5.1-codex-max", metadata=_retired(replacement)
    )
    # The replacement exists but is not enabled for the organization.
    session.add_all(
        [
            retired,
            _catalog(None, "openai", "gpt-5.6-sol"),
            _auto_migrate(svc_organization.id),
        ]
    )
    await session.commit()

    # Fails open: the run keeps the pinned model and the provider decides.
    assert (
        await AgentCatalogService(session=session).resolve_model_replacement(
            role=_user_role(svc_organization.id),
            model_provider="openai",
            model_name="gpt-5.1-codex-max",
            catalog_id=retired.id,
        )
        is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize("stored", [None, False])
async def test_resolve_model_replacement_requires_org_opt_in(
    session: AsyncSession,
    svc_organization: Organization,
    stored: bool | None,
) -> None:
    retired = _catalog(
        None, "openai", "gpt-5.1-codex-max", metadata=_retired("gpt-5.6-sol")
    )
    replacement = _catalog(None, "openai", "gpt-5.6-sol")
    session.add_all([retired, replacement])
    if stored is not None:
        session.add(_auto_migrate(svc_organization.id, enabled=stored))
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=replacement.id))
    await session.commit()

    assert (
        await AgentCatalogService(session=session).resolve_model_replacement(
            role=_user_role(svc_organization.id),
            model_provider="openai",
            model_name="gpt-5.1-codex-max",
            catalog_id=retired.id,
        )
        is None
    )


@pytest.mark.anyio
async def test_resolve_model_replacement_uses_retired_list_when_no_row_exists(
    session: AsyncSession,
    svc_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retired_key = ModelKey("openai", "gpt-5.1-codex-max")
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.get_retired_platform_models",
        lambda: {
            retired_key: ModelLifecycle(
                retires_on=date(2000, 1, 1), replacement="gpt-5.6-sol"
            )
        },
    )
    replacement = _catalog(None, "openai", "gpt-5.6-sol")
    session.add(replacement)
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=replacement.id))
    session.add(_auto_migrate(svc_organization.id))
    await session.commit()
    service = AgentCatalogService(session=session)

    # A catalog_id synced or copied from a deployment that still has the row.
    stale = await service.resolve_model_replacement(
        role=_user_role(svc_organization.id),
        model_provider="openai",
        model_name="gpt-5.1-codex-max",
        catalog_id=uuid.uuid4(),
    )
    nulled = await service.resolve_model_replacement(
        role=_user_role(svc_organization.id),
        model_provider="openai",
        model_name="gpt-5.1-codex-max",
        catalog_id=None,
    )

    assert stale == ModelReplacement(
        model_provider="openai", model_name="gpt-5.6-sol", catalog_id=replacement.id
    )
    assert nulled == ModelReplacement(
        model_provider="openai", model_name="gpt-5.6-sol", catalog_id=None
    )


@pytest.mark.anyio
async def test_resolve_model_replacement_trusts_the_catalog_row_identity(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    retired = _catalog(
        None, "openai", "gpt-5.1-codex-max", metadata=_retired("gpt-5.6-sol")
    )
    replacement = _catalog(None, "openai", "gpt-5.6-sol")
    session.add_all([retired, replacement])
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=replacement.id))
    session.add(_auto_migrate(svc_organization.id))
    await session.commit()

    # Caller fields disagree with the row the catalog_id names.
    resolved = await AgentCatalogService(session=session).resolve_model_replacement(
        role=_user_role(svc_organization.id),
        model_provider="anthropic",
        model_name="stale-name",
        catalog_id=retired.id,
    )

    assert resolved == ModelReplacement(
        model_provider="openai", model_name="gpt-5.6-sol", catalog_id=replacement.id
    )


@pytest.mark.anyio
async def test_resolve_model_replacement_prefers_bundled_retirement_for_kept_rows(
    session: AsyncSession,
    svc_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A row kept for a reference still names its first, now retired, successor.
    kept = _catalog(
        None, "openai", "gpt-5.1-codex-max", metadata=_retired("gpt-5.6-sol")
    )
    successor = _catalog(None, "openai", "gpt-6-sol")
    session.add_all([kept, successor])
    await session.flush()
    session.add(_enable(org_id=svc_organization.id, catalog_id=successor.id))
    session.add(_auto_migrate(svc_organization.id))
    await session.commit()
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.get_retired_platform_models",
        lambda: {
            ModelKey("openai", "gpt-5.1-codex-max"): ModelLifecycle(
                retires_on=date(2000, 1, 1), replacement="gpt-6-sol"
            )
        },
    )

    resolved = await AgentCatalogService(session=session).resolve_model_replacement(
        role=_user_role(svc_organization.id),
        model_provider="openai",
        model_name="gpt-5.1-codex-max",
        catalog_id=kept.id,
    )

    assert resolved == ModelReplacement(
        model_provider="openai", model_name="gpt-6-sol", catalog_id=successor.id
    )
