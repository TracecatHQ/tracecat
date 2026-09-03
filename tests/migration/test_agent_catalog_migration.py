"""Tests for the LLM providers v2 expansion-only backfill migration.

These tests call the migration's upgrade helpers directly against the shared
test database rather than running alembic end-to-end — the helpers accept a
plain SQLAlchemy ``Connection`` so they can be exercised in isolation, which
keeps per-test runtime low while still covering the SQL paths.

The v2 backfill is decrypt-free by design: ``organization_secret.encrypted_keys``
is copied byte-for-byte into ``encrypted_config`` on the new catalog and
custom-provider rows. The migration never imports
``TRACECAT__DB_ENCRYPTION_KEY``, so tests don't need to set one — the
``encrypt_keyvalues`` helper still needs *some* key when seeding fixtures,
but that key is never reused by the migration code.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from typing import Any

import orjson
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentPreset,
    AgentPresetVersion,
    Organization,
    OrganizationSecret,
    OrganizationSetting,
    Workspace,
)
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "96470fdcc686_v2_backfill_catalog_and_access.py"
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def fernet_key() -> str:
    """A Fernet key used only by the test fixtures to seed encrypted secrets.

    The migration itself does not decrypt anything, so this key is purely a
    fixture concern. We assert ciphertext equality between source secret and
    destination ``encrypted_config`` columns to prove the byte-copy property.
    """
    return Fernet.generate_key().decode()


@pytest.fixture
def migration() -> Any:
    """Import the migration module from its file path.

    Alembic version files aren't part of a real Python package, so we load
    by file path rather than dotted import. The module is cached under a
    synthetic name so the import is done once across tests in the session.
    """
    module_name = "_v2_backfill_migration_under_test"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def fresh_org(session: AsyncSession) -> Organization:
    """A brand-new Organization scoped to this test's transaction.

    Using a unique id per test avoids collisions with the shared ``svc_organization``
    fixture and keeps each test's inserts self-contained so
    ``ON CONFLICT DO NOTHING`` assertions are meaningful.
    """
    org = Organization(
        id=uuid.uuid4(),
        name="Migration Test Org",
        slug=f"mig-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def fresh_workspace(session: AsyncSession, fresh_org: Organization) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name="mig-workspace",
        organization_id=fresh_org.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def _seed_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    keyvalues: dict[str, str],
    encryption_key: str,
) -> bytes:
    """Seed an ``organization_secret`` row and return its ciphertext blob."""
    encrypted = encrypt_keyvalues(
        [SecretKeyValue(key=k, value=SecretStr(v)) for k, v in keyvalues.items()],
        key=encryption_key,
    )
    secret = OrganizationSecret(
        organization_id=org_id,
        name=name,
        type=SecretType.CUSTOM.value,
        encrypted_keys=encrypted,
    )
    session.add(secret)
    await session.commit()
    return encrypted


async def _seed_platform_catalog(
    session: AsyncSession, *, entries: list[tuple[str, str]]
) -> list[uuid.UUID]:
    """Insert platform catalog rows (org_id IS NULL) and return their ids."""
    from datetime import UTC, datetime

    ids: list[uuid.UUID] = []
    for provider, name in entries:
        row = AgentCatalog(
            id=uuid.uuid4(),
            organization_id=None,
            custom_provider_id=None,
            model_provider=provider,
            model_name=name,
            model_metadata={},
            last_refreshed_at=datetime.now(UTC),
        )
        session.add(row)
        ids.append(row.id)
    await session.commit()
    return ids


async def _run_upgrade(session: AsyncSession, migration: Any) -> None:
    """Invoke the migration's upgrade helpers against the test session.

    We bypass ``alembic.op.get_bind()`` entirely — the migration's internal
    helpers already accept a raw ``Connection``, so we call them the same
    way ``upgrade()`` does but against the connection backing the test
    session. Anything committed inside the helpers joins the test's outer
    savepoint.
    """
    conn = await session.connection()

    def _invoke(sync_conn: sa.engine.Connection) -> None:
        migration._seed_platform_catalog_rows(sync_conn)
        for org_id in migration._orgs_with_any_provider_secret(sync_conn):
            migration._backfill_org_provider_data(sync_conn, org_id=org_id)
        migration._backfill_preset_catalog_ids(
            sync_conn, table=migration.agent_preset_tbl, label="agent_preset"
        )
        migration._backfill_preset_catalog_ids(
            sync_conn,
            table=migration.agent_preset_version_tbl,
            label="agent_preset_version",
        )

    await conn.run_sync(_invoke)


# ---------------------------------------------------------------------------
# Platform catalog seeding
# ---------------------------------------------------------------------------


def test_platform_catalog_loader_fails_fast_for_missing_invalid_or_empty(
    migration: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", missing)
    with pytest.raises(RuntimeError, match="missing or unreadable"):
        migration._load_platform_catalog_entries()

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", invalid)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        migration._load_platform_catalog_entries()

    empty = tmp_path / "empty.json"
    empty.write_text('{"models":[]}', encoding="utf-8")
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", empty)
    with pytest.raises(RuntimeError, match="non-empty models list"):
        migration._load_platform_catalog_entries()

    invalid_entry = tmp_path / "invalid-entry.json"
    invalid_entry.write_text(
        '{"models":[{"model_provider":"openai"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", invalid_entry)
    with pytest.raises(RuntimeError, match="invalid model_name"):
        migration._load_platform_catalog_entries()


@pytest.mark.anyio
async def test_upgrade_seeds_platform_catalog_before_grants_and_preset_linking(
    session: AsyncSession,
    fresh_org: Organization,
    fresh_workspace: Workspace,
    fernet_key: str,
    migration: Any,
) -> None:
    source_entry = next(
        entry
        for entry in migration._load_platform_catalog_entries()
        if entry["model_provider"] == "openai"
    )
    model_provider = source_entry["model_provider"]
    model_name = source_entry["model_name"]

    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-openai-credentials",
        keyvalues={"OPENAI_API_KEY": "sk-..."},
        encryption_key=fernet_key,
    )

    preset = AgentPreset(
        workspace_id=fresh_workspace.id,
        slug="p1",
        name="Preset 1",
        model_provider=model_provider,
        model_name=model_name,
        retries=3,
    )
    version = AgentPresetVersion(
        workspace_id=fresh_workspace.id,
        preset_id=uuid.uuid4(),
        version=1,
        model_provider=model_provider,
        model_name=model_name,
        retries=3,
    )
    session.add(preset)
    await session.flush()
    version.preset_id = preset.id
    session.add(version)
    await session.commit()

    await _run_upgrade(session, migration)

    catalog_row = (
        await session.execute(
            sa.select(AgentCatalog).where(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.custom_provider_id.is_(None),
                AgentCatalog.model_provider == model_provider,
                AgentCatalog.model_name == model_name,
            )
        )
    ).scalar_one()
    assert catalog_row.model_metadata == source_entry["metadata"]

    access_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_model_access_tbl)
        .where(
            migration.agent_model_access_tbl.c.organization_id == fresh_org.id,
            migration.agent_model_access_tbl.c.catalog_id == catalog_row.id,
            migration.agent_model_access_tbl.c.workspace_id.is_(None),
        )
    )
    assert access_count == 1

    await session.refresh(preset)
    await session.refresh(version)
    assert preset.catalog_id == catalog_row.id
    assert version.catalog_id == catalog_row.id


# ---------------------------------------------------------------------------
# Cloud provider migration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cloud_provider_creates_catalog_with_copied_ciphertext(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    blob = await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-bedrock-credentials",
        keyvalues={
            "AWS_ACCESS_KEY_ID": "AKIA...",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        },
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    rows = (
        (
            await session.execute(
                sa.select(migration.agent_catalog_tbl).where(
                    migration.agent_catalog_tbl.c.organization_id == fresh_org.id,
                    migration.agent_catalog_tbl.c.model_provider == "bedrock",
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["model_name"] == "bedrock"
    assert row["custom_provider_id"] is None
    # Ciphertext copied byte-for-byte from organization_secret.
    assert bytes(row["encrypted_config"]) == bytes(blob)

    access_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_model_access_tbl)
        .where(
            migration.agent_model_access_tbl.c.organization_id == fresh_org.id,
            migration.agent_model_access_tbl.c.catalog_id == row["id"],
            migration.agent_model_access_tbl.c.workspace_id.is_(None),
        )
    )
    assert access_count == 1


@pytest.mark.anyio
async def test_all_cloud_providers_get_one_row_each(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    seeded: dict[str, bytes] = {}
    for provider, payload in [
        ("bedrock", {"AWS_REGION": "us-east-1"}),
        ("azure_openai", {"AZURE_API_KEY": "sk-..."}),
        ("azure_ai", {"AZURE_API_KEY": "sk-..."}),
        ("vertex_ai", {"GOOGLE_CLOUD_PROJECT": "my-project"}),
    ]:
        seeded[provider] = await _seed_secret(
            session,
            org_id=fresh_org.id,
            name=f"agent-{provider}-credentials",
            keyvalues=payload,
            encryption_key=fernet_key,
        )

    await _run_upgrade(session, migration)

    rows = (
        (
            await session.execute(
                sa.select(migration.agent_catalog_tbl).where(
                    migration.agent_catalog_tbl.c.organization_id == fresh_org.id,
                )
            )
        )
        .mappings()
        .all()
    )
    by_provider = {r["model_provider"]: r for r in rows}
    assert set(by_provider) == set(seeded)
    for provider, blob in seeded.items():
        row = by_provider[provider]
        assert row["model_name"] == provider
        assert bytes(row["encrypted_config"]) == bytes(blob)


@pytest.mark.anyio
async def test_no_cloud_secret_no_catalog_row(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    # Only seed a direct-provider secret so the org appears in the loop
    # but has no cloud-provider secrets.
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-openai-credentials",
        keyvalues={"OPENAI_API_KEY": "sk-..."},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_catalog_tbl)
        .where(
            migration.agent_catalog_tbl.c.organization_id == fresh_org.id,
            migration.agent_catalog_tbl.c.model_provider.in_(
                list(migration.CLOUD_PROVIDERS)
            ),
        )
    )
    assert count == 0


# ---------------------------------------------------------------------------
# Custom provider migration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_custom_provider_materializes_row_and_catalog(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    blob = await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-custom-model-provider-credentials",
        keyvalues={
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
            "CUSTOM_MODEL_PROVIDER_API_KEY": "sk-super-secret",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "my-house-model",
        },
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    providers = (
        (
            await session.execute(
                sa.select(AgentCustomProvider).where(
                    AgentCustomProvider.organization_id == fresh_org.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(providers) == 1
    provider = providers[0]
    assert provider.display_name == migration.CUSTOM_PROVIDER_DISPLAY_NAME
    assert provider.base_url is None
    assert provider.passthrough is False
    assert provider.api_key_header is None
    assert provider.encrypted_config is not None
    assert bytes(provider.encrypted_config) == bytes(blob)

    # Linked catalog row, ciphertext also copied there for parity.
    catalog_row = (
        await session.execute(
            sa.select(migration.agent_catalog_tbl).where(
                migration.agent_catalog_tbl.c.organization_id == fresh_org.id,
                migration.agent_catalog_tbl.c.custom_provider_id == provider.id,
            )
        )
    ).one()
    assert catalog_row.model_provider == migration.CUSTOM_PROVIDER_SLUG
    assert catalog_row.model_name == migration.CUSTOM_PROVIDER_MODEL_NAME
    assert bytes(catalog_row.encrypted_config) == bytes(blob)

    access_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_model_access_tbl)
        .where(
            migration.agent_model_access_tbl.c.organization_id == fresh_org.id,
            migration.agent_model_access_tbl.c.catalog_id == catalog_row.id,
            migration.agent_model_access_tbl.c.workspace_id.is_(None),
        )
    )
    assert access_count == 1


@pytest.mark.anyio
async def test_custom_provider_rerun_does_not_duplicate(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-custom-model-provider-credentials",
        keyvalues={"CUSTOM_MODEL_PROVIDER_API_KEY": "sk-..."},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)
    await _run_upgrade(session, migration)

    provider_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentCustomProvider)
        .where(AgentCustomProvider.organization_id == fresh_org.id)
    )
    assert provider_count == 1
    catalog_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_catalog_tbl)
        .where(
            migration.agent_catalog_tbl.c.organization_id == fresh_org.id,
            migration.agent_catalog_tbl.c.model_provider
            == migration.CUSTOM_PROVIDER_SLUG,
        )
    )
    assert catalog_count == 1


@pytest.mark.anyio
async def test_no_custom_secret_no_custom_provider_row(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    # Direct-provider secret only; custom provider should not be created.
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-openai-credentials",
        keyvalues={"OPENAI_API_KEY": "sk-..."},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentCustomProvider)
        .where(AgentCustomProvider.organization_id == fresh_org.id)
    )
    assert count == 0


@pytest.mark.anyio
async def test_custom_provider_preset_links_to_custom_catalog_row(
    session: AsyncSession,
    fresh_org: Organization,
    fresh_workspace: Workspace,
    fernet_key: str,
    migration: Any,
) -> None:
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-custom-model-provider-credentials",
        keyvalues={"CUSTOM_MODEL_PROVIDER_API_KEY": "sk-..."},
        encryption_key=fernet_key,
    )
    preset = AgentPreset(
        workspace_id=fresh_workspace.id,
        slug="custom-preset",
        name="Custom Preset",
        model_provider=migration.CUSTOM_PROVIDER_SLUG,
        model_name=migration.CUSTOM_PROVIDER_MODEL_NAME,
        retries=3,
    )
    version = AgentPresetVersion(
        workspace_id=fresh_workspace.id,
        preset_id=uuid.uuid4(),
        version=1,
        model_provider=migration.CUSTOM_PROVIDER_SLUG,
        model_name=migration.CUSTOM_PROVIDER_MODEL_NAME,
        retries=3,
    )
    session.add(preset)
    await session.flush()
    version.preset_id = preset.id
    session.add(version)
    await session.commit()

    await _run_upgrade(session, migration)

    catalog_row = (
        await session.execute(
            sa.select(migration.agent_catalog_tbl).where(
                migration.agent_catalog_tbl.c.organization_id == fresh_org.id,
                migration.agent_catalog_tbl.c.model_provider
                == migration.CUSTOM_PROVIDER_SLUG,
                migration.agent_catalog_tbl.c.model_name
                == migration.CUSTOM_PROVIDER_MODEL_NAME,
            )
        )
    ).one()

    await session.refresh(preset)
    await session.refresh(version)
    assert preset.catalog_id == catalog_row.id
    assert version.catalog_id == catalog_row.id


# ---------------------------------------------------------------------------
# Direct-provider access grants
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_direct_provider_secret_grants_access_to_all_platform_rows(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-openai-credentials",
        keyvalues={"OPENAI_API_KEY": "sk-foo"},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    openai_catalog_ids = set(
        (
            await session.execute(
                sa.select(migration.agent_catalog_tbl.c.id).where(
                    migration.agent_catalog_tbl.c.organization_id.is_(None),
                    migration.agent_catalog_tbl.c.custom_provider_id.is_(None),
                    migration.agent_catalog_tbl.c.model_provider == "openai",
                )
            )
        ).scalars()
    )
    assert openai_catalog_ids

    granted_catalog_ids = set(
        (
            await session.execute(
                sa.select(migration.agent_model_access_tbl.c.catalog_id).where(
                    migration.agent_model_access_tbl.c.organization_id == fresh_org.id,
                    migration.agent_model_access_tbl.c.workspace_id.is_(None),
                )
            )
        ).scalars()
    )
    assert granted_catalog_ids == openai_catalog_ids


@pytest.mark.anyio
async def test_direct_provider_secret_without_matching_catalog_rows_gets_no_grants(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "_load_platform_catalog_entries",
        lambda: [
            {
                "model_provider": "google",
                "model_name": "gemini-source-row",
                "metadata": {"provider": "google"},
            }
        ],
    )
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-gemini-credentials",
        keyvalues={"GEMINI_API_KEY": "sk-foo"},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    grant_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_model_access_tbl)
        .where(migration.agent_model_access_tbl.c.organization_id == fresh_org.id)
    )
    assert grant_count == 0

    google_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_catalog_tbl)
        .where(
            migration.agent_catalog_tbl.c.organization_id.is_(None),
            migration.agent_catalog_tbl.c.model_provider == "google",
        )
    )
    assert google_count == 1

    gemini_count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(migration.agent_catalog_tbl)
        .where(
            migration.agent_catalog_tbl.c.organization_id.is_(None),
            migration.agent_catalog_tbl.c.model_provider == "gemini",
        )
    )
    assert gemini_count == 0


@pytest.mark.anyio
async def test_gemini_secret_grants_access_to_gemini_source_catalog_rows(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "_load_platform_catalog_entries",
        lambda: [
            {
                "model_provider": "google",
                "model_name": "gemini-google-row",
                "metadata": {"provider": "google"},
            },
            {
                "model_provider": "gemini",
                "model_name": "gemini-source-row",
                "metadata": {"provider": "gemini"},
            },
        ],
    )
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-gemini-credentials",
        keyvalues={"GEMINI_API_KEY": "sk-foo"},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    platform_rows = (
        (
            await session.execute(
                sa.select(
                    migration.agent_catalog_tbl.c.id,
                    migration.agent_catalog_tbl.c.model_provider,
                    migration.agent_catalog_tbl.c.model_name,
                ).where(migration.agent_catalog_tbl.c.organization_id.is_(None))
            )
        )
        .mappings()
        .all()
    )
    rows_by_provider = {row["model_provider"]: row for row in platform_rows}
    assert rows_by_provider["google"]["model_name"] == "gemini-google-row"
    assert rows_by_provider["gemini"]["model_name"] == "gemini-source-row"

    granted_catalog_ids = set(
        (
            await session.execute(
                sa.select(migration.agent_model_access_tbl.c.catalog_id).where(
                    migration.agent_model_access_tbl.c.organization_id == fresh_org.id,
                    migration.agent_model_access_tbl.c.workspace_id.is_(None),
                )
            )
        ).scalars()
    )
    assert granted_catalog_ids == {rows_by_provider["gemini"]["id"]}


# ---------------------------------------------------------------------------
# Failure behavior
# ---------------------------------------------------------------------------


def test_org_backfill_failure_is_not_swallowed(
    migration: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_org = uuid.uuid4()
    second_org = uuid.uuid4()
    visited: list[uuid.UUID] = []

    monkeypatch.setattr(
        migration,
        "_orgs_with_any_provider_secret",
        lambda _conn: [first_org, second_org],
    )

    def _fail_org_backfill(_conn: object, *, org_id: uuid.UUID) -> None:
        visited.append(org_id)
        raise RuntimeError("boom")

    monkeypatch.setattr(
        migration,
        "_backfill_org_provider_data",
        _fail_org_backfill,
    )
    monkeypatch.setattr(migration, "_seed_platform_catalog_rows", lambda _conn: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())

    with pytest.raises(RuntimeError, match="boom"):
        migration.upgrade()

    assert visited == [first_org]


# ---------------------------------------------------------------------------
# Preset backfill
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_preset_links_to_platform_catalog_when_unambiguous(
    session: AsyncSession,
    fresh_workspace: Workspace,
    migration: Any,
) -> None:
    platform_ids = await _seed_platform_catalog(
        session, entries=[("openai", "gpt-5-mini-2025-08-07")]
    )
    preset = AgentPreset(
        workspace_id=fresh_workspace.id,
        slug="p1",
        name="Preset 1",
        model_provider="openai",
        model_name="gpt-5-mini-2025-08-07",
        retries=3,
    )
    version = AgentPresetVersion(
        workspace_id=fresh_workspace.id,
        preset_id=uuid.uuid4(),  # placeholder; will set below
        version=1,
        model_provider="openai",
        model_name="gpt-5-mini-2025-08-07",
        retries=3,
    )
    session.add(preset)
    await session.flush()
    version.preset_id = preset.id
    session.add(version)
    await session.commit()

    await _run_upgrade(session, migration)

    await session.refresh(preset)
    await session.refresh(version)
    assert preset.catalog_id == platform_ids[0]
    assert version.catalog_id == platform_ids[0]


@pytest.mark.anyio
async def test_preset_prefers_platform_row_on_ambiguous_match(
    session: AsyncSession,
    fresh_org: Organization,
    fresh_workspace: Workspace,
    migration: Any,
) -> None:
    from datetime import UTC, datetime

    platform_row = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        custom_provider_id=None,
        model_provider="openai",
        model_name="shared-model",
        model_metadata={},
        last_refreshed_at=datetime.now(UTC),
    )
    org_row = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=fresh_org.id,
        custom_provider_id=None,
        model_provider="openai",
        model_name="shared-model",
        model_metadata={},
        last_refreshed_at=datetime.now(UTC),
    )
    session.add_all([platform_row, org_row])
    await session.flush()

    preset = AgentPreset(
        workspace_id=fresh_workspace.id,
        slug="p1",
        name="Preset 1",
        model_provider="openai",
        model_name="shared-model",
        retries=3,
    )
    session.add(preset)
    await session.commit()

    await _run_upgrade(session, migration)

    await session.refresh(preset)
    assert preset.catalog_id == platform_row.id


@pytest.mark.anyio
async def test_preset_with_no_match_stays_unlinked(
    session: AsyncSession,
    fresh_workspace: Workspace,
    migration: Any,
) -> None:
    preset = AgentPreset(
        workspace_id=fresh_workspace.id,
        slug="p1",
        name="Preset 1",
        model_provider="openai",
        model_name="nonexistent-model",
        retries=3,
    )
    session.add(preset)
    await session.commit()

    await _run_upgrade(session, migration)

    await session.refresh(preset)
    assert preset.catalog_id is None


# ---------------------------------------------------------------------------
# Default model setting backfill
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_default_model_setting_resolves_to_catalog_id(
    session: AsyncSession,
    fresh_org: Organization,
    migration: Any,
) -> None:
    platform_ids = await _seed_platform_catalog(
        session, entries=[("openai", "gpt-5-2025-08-07")]
    )
    setting = OrganizationSetting(
        organization_id=fresh_org.id,
        key="agent_default_model",
        value=orjson.dumps("gpt-5-2025-08-07"),
        value_type="JSON",
        is_encrypted=False,
    )
    session.add(setting)
    await session.commit()

    # Default-model resolution only runs for orgs that have at least one
    # provider secret, since the upgrade loop is keyed on that. Seed a
    # direct-provider secret so this org is in scope.
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-openai-credentials",
        keyvalues={"OPENAI_API_KEY": "sk-..."},
        encryption_key=Fernet.generate_key().decode(),
    )

    await _run_upgrade(session, migration)

    resolved = (
        await session.execute(
            sa.select(OrganizationSetting).where(
                OrganizationSetting.organization_id == fresh_org.id,
                OrganizationSetting.key == "agent_default_model_catalog_id",
            )
        )
    ).scalar_one()
    assert orjson.loads(resolved.value) == str(platform_ids[0])


@pytest.mark.anyio
async def test_default_model_setting_unknown_model_is_left_unset(
    session: AsyncSession,
    fresh_org: Organization,
    migration: Any,
) -> None:
    await _seed_platform_catalog(session, entries=[("openai", "gpt-5-2025-08-07")])
    setting = OrganizationSetting(
        organization_id=fresh_org.id,
        key="agent_default_model",
        value=orjson.dumps("some-removed-model"),
        value_type="JSON",
        is_encrypted=False,
    )
    session.add(setting)
    await session.commit()

    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-openai-credentials",
        keyvalues={"OPENAI_API_KEY": "sk-..."},
        encryption_key=Fernet.generate_key().decode(),
    )

    await _run_upgrade(session, migration)

    exists = await session.scalar(
        sa.select(sa.func.count())
        .select_from(OrganizationSetting)
        .where(
            OrganizationSetting.organization_id == fresh_org.id,
            OrganizationSetting.key == "agent_default_model_catalog_id",
        )
    )
    assert exists == 0


# ---------------------------------------------------------------------------
# Idempotency + preservation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rerunning_upgrade_is_a_noop(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-bedrock-credentials",
        keyvalues={"AWS_REGION": "us-east-1"},
        encryption_key=fernet_key,
    )
    await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-custom-model-provider-credentials",
        keyvalues={"CUSTOM_MODEL_PROVIDER_API_KEY": "sk-..."},
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)
    first_counts = await _row_counts(session, migration, fresh_org.id)
    await _run_upgrade(session, migration)
    second_counts = await _row_counts(session, migration, fresh_org.id)

    assert first_counts == second_counts


@pytest.mark.anyio
async def test_credential_keys_are_not_modified(
    session: AsyncSession,
    fresh_org: Organization,
    fernet_key: str,
    migration: Any,
) -> None:
    """Expansion-only guarantee: the source secret blob is untouched."""
    original = {
        "AWS_ACCESS_KEY_ID": "AKIA...",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_REGION": "us-east-1",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-opus-4-5",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307",
    }
    blob_before = await _seed_secret(
        session,
        org_id=fresh_org.id,
        name="agent-bedrock-credentials",
        keyvalues=original,
        encryption_key=fernet_key,
    )

    await _run_upgrade(session, migration)

    secret = (
        await session.execute(
            sa.select(OrganizationSecret).where(
                OrganizationSecret.organization_id == fresh_org.id,
                OrganizationSecret.name == "agent-bedrock-credentials",
            )
        )
    ).scalar_one()
    # Ciphertext bytes are byte-for-byte unchanged in the source row.
    assert bytes(secret.encrypted_keys) == bytes(blob_before)


async def _row_counts(
    session: AsyncSession, migration: Any, org_id: uuid.UUID
) -> dict[str, int]:
    async def _count(stmt: sa.sql.Select[Any]) -> int:
        result = await session.scalar(stmt)
        return int(result or 0)

    return {
        "catalog": await _count(
            sa.select(sa.func.count())
            .select_from(migration.agent_catalog_tbl)
            .where(migration.agent_catalog_tbl.c.organization_id == org_id)
        ),
        "access": await _count(
            sa.select(sa.func.count())
            .select_from(migration.agent_model_access_tbl)
            .where(migration.agent_model_access_tbl.c.organization_id == org_id)
        ),
        "custom_provider": await _count(
            sa.select(sa.func.count())
            .select_from(AgentCustomProvider)
            .where(AgentCustomProvider.organization_id == org_id)
        ),
    }
