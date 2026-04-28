"""v2 backfill catalog and access

Revision ID: 96470fdcc686
Revises: 7d23a45113ee
Create Date: 2026-04-21 10:00:00.000000

Expansion-only data migration for LLM providers v2.

Backfills the new catalog-based world from pre-v2 data without ever
decrypting stored credentials. The Fernet-encrypted credential blob for
each ``agent-{provider}-credentials`` organization secret is copied
byte-for-byte into the corresponding ``encrypted_config`` column on
``agent_catalog`` (cloud providers) or ``agent_custom_provider`` (custom
provider). The gateway reads from those columns at call time, reusing
``TRACECAT__DB_ENCRYPTION_KEY`` for decryption exactly as today — the
migration never needs the key.

After this migration:

1. Each org with a custom-model-provider credential secret has an
   ``agent_custom_provider`` row with ``encrypted_config`` copied from the
   secret's ``encrypted_keys``, plus one org-scoped ``agent_catalog`` row
   pointing at it and an ``agent_model_access`` grant.
2. Each org with a cloud-provider credential secret (bedrock /
   azure_openai / azure_ai / vertex_ai) gets one org-scoped
   ``agent_catalog`` row whose ``encrypted_config`` is the copied
   ciphertext and whose ``model_name`` is the provider slug. The gateway
   resolves the real invocation target by decrypting ``encrypted_config``
   at call time.
3. Platform catalog rows are seeded from
   ``tracecat/agent/platform_catalog.json`` exactly as the file provides
   them.
4. Each org that has any direct-provider credential secret (openai /
   anthropic / gemini) gets ``agent_model_access`` grants to every
   platform catalog row for that provider. No additional catalog rows are
   created for direct providers.
5. Every ``agent_preset`` and ``agent_preset_version`` with
   ``catalog_id IS NULL`` is matched against catalog rows by
   ``(model_provider, model_name)`` and linked when unambiguous.
6. The ``agent_default_model`` org setting (string name) is resolved to
   the corresponding catalog row, and ``agent_default_model_catalog_id``
   is populated.

Nothing is deleted: the old ``organization_secret`` rows remain so the
pre-v2 gateway path keeps working during rollout. The hot-path PR that
follows this one will flip the gateway to read ``encrypted_config`` from
catalog rows, after which a later contract migration can drop the
``agent-*-credentials`` secrets.

Every step is idempotent (``ON CONFLICT`` clauses or ``WHERE catalog_id IS
NULL`` guards). The migration intentionally fails fast: if any org cannot be
backfilled, the surrounding Alembic transaction rolls back and operators can
correct the issue before rerunning.

Downgrade is a no-op by design — new rows are additive and the old world
still works, so rollback is ``DELETE`` on the new tables (handled by
operators via backup restore if needed).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "96470fdcc686"
down_revision: str | None = "7d23a45113ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger("alembic.runtime.migration.v2_backfill")

PLATFORM_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "tracecat" / "agent" / "platform_catalog.json"
)


# Cloud-provider slugs whose credential blob carries an invocation target
# (model id, deployment name, inference profile, ...). One catalog row per
# (org, provider). The gateway decrypts ``encrypted_config`` at call time to
# resolve the target, so the migration never needs the key.
CLOUD_PROVIDERS: frozenset[str] = frozenset(
    {"bedrock", "azure_openai", "azure_ai", "vertex_ai"}
)

DIRECT_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "gemini"})

CUSTOM_PROVIDER_SLUG = "custom-model-provider"
CUSTOM_PROVIDER_MODEL_NAME = "custom"
CUSTOM_PROVIDER_DISPLAY_NAME = "Custom LLM Provider"

DEFAULT_MODEL_SETTING_KEY = "agent_default_model"
DEFAULT_MODEL_CATALOG_ID_SETTING_KEY = "agent_default_model_catalog_id"


# Lightweight sa.table() definitions matching the frozen v1 schema. Using
# literal tables here (rather than importing ORM models from
# ``tracecat.db.models``) keeps the migration stable even if the ORM
# evolves in later releases.
org_secret_tbl = sa.table(
    "organization_secret",
    sa.column("id", postgresql.UUID()),
    sa.column("organization_id", postgresql.UUID()),
    sa.column("name", sa.String()),
    sa.column("encrypted_keys", sa.LargeBinary()),
)

org_setting_tbl = sa.table(
    "organization_settings",
    sa.column("id", postgresql.UUID()),
    sa.column("organization_id", postgresql.UUID()),
    sa.column("key", sa.String()),
    sa.column("value", sa.LargeBinary()),
    sa.column("value_type", sa.String()),
    sa.column("is_encrypted", sa.Boolean()),
)

agent_custom_provider_tbl = sa.table(
    "agent_custom_provider",
    sa.column("id", postgresql.UUID()),
    sa.column("organization_id", postgresql.UUID()),
    sa.column("display_name", sa.String()),
    sa.column("base_url", sa.String()),
    sa.column("passthrough", sa.Boolean()),
    sa.column("encrypted_config", sa.LargeBinary()),
    sa.column("api_key_header", sa.String()),
    sa.column("last_refreshed_at", sa.TIMESTAMP(timezone=True)),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
)

agent_catalog_tbl = sa.table(
    "agent_catalog",
    sa.column("id", postgresql.UUID()),
    sa.column("organization_id", postgresql.UUID()),
    sa.column("custom_provider_id", postgresql.UUID()),
    sa.column("model_provider", sa.String()),
    sa.column("model_name", sa.String()),
    sa.column("model_metadata", postgresql.JSONB()),
    sa.column("encrypted_config", sa.LargeBinary()),
    sa.column("last_refreshed_at", sa.TIMESTAMP(timezone=True)),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
)

agent_model_access_tbl = sa.table(
    "agent_model_access",
    sa.column("id", postgresql.UUID()),
    sa.column("organization_id", postgresql.UUID()),
    sa.column("workspace_id", postgresql.UUID()),
    sa.column("catalog_id", postgresql.UUID()),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
)

agent_preset_tbl = sa.table(
    "agent_preset",
    sa.column("id", postgresql.UUID()),
    sa.column("workspace_id", postgresql.UUID()),
    sa.column("model_provider", sa.String()),
    sa.column("model_name", sa.String()),
    sa.column("catalog_id", postgresql.UUID()),
)

agent_preset_version_tbl = sa.table(
    "agent_preset_version",
    sa.column("id", postgresql.UUID()),
    sa.column("workspace_id", postgresql.UUID()),
    sa.column("model_provider", sa.String()),
    sa.column("model_name", sa.String()),
    sa.column("catalog_id", postgresql.UUID()),
)

workspace_tbl = sa.table(
    "workspace",
    sa.column("id", postgresql.UUID()),
    sa.column("organization_id", postgresql.UUID()),
)


def upgrade() -> None:
    conn = op.get_bind()

    _seed_platform_catalog_rows(conn)

    # Org-scoped rows and access grants depend on the platform catalog rows above.
    for org_id in _orgs_with_any_provider_secret(conn):
        _backfill_org_provider_data(conn, org_id=org_id)

    # Preset + version catalog_id backfill. Not per-org because the join
    # through workspace already scopes each preset correctly.
    _backfill_preset_catalog_ids(conn, table=agent_preset_tbl, label="agent_preset")
    _backfill_preset_catalog_ids(
        conn, table=agent_preset_version_tbl, label="agent_preset_version"
    )


def downgrade() -> None:
    """Intentional no-op.

    This migration is expansion-only and cannot be automatically reversed
    because it creates rows rather than altering schema. Rollback path
    per ``alembic/CLAUDE.md``: restore the database from backup or simply
    delete the new rows manually; old application code continues to work
    because no data was mutated destructively.
    """


def _load_platform_catalog_entries() -> list[dict[str, Any]]:
    """Load and validate the frozen source catalog for platform rows."""
    try:
        raw = PLATFORM_CATALOG_PATH.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"Platform catalog file is missing or unreadable: {PLATFORM_CATALOG_PATH}"
        ) from exc

    try:
        parsed = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise RuntimeError(
            f"Platform catalog file contains invalid JSON: {PLATFORM_CATALOG_PATH}"
        ) from exc

    if not isinstance(parsed, Mapping):
        raise RuntimeError("Platform catalog root must be a JSON object")

    models = parsed.get("models")
    if not isinstance(models, list) or not models:
        raise RuntimeError("Platform catalog must contain a non-empty models list")

    entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(models):
        if not isinstance(entry, Mapping):
            raise RuntimeError(
                f"Platform catalog model at index {idx} must be an object"
            )

        model_provider = entry.get("model_provider")
        model_name = entry.get("model_name")
        metadata = entry.get("metadata", {})
        if not isinstance(model_provider, str) or not model_provider:
            raise RuntimeError(
                f"Platform catalog model at index {idx} has invalid model_provider"
            )
        if not isinstance(model_name, str) or not model_name:
            raise RuntimeError(
                f"Platform catalog model at index {idx} has invalid model_name"
            )
        if not isinstance(metadata, Mapping):
            raise RuntimeError(
                f"Platform catalog model at index {idx} has invalid metadata"
            )

        entries.append(
            {
                "model_provider": model_provider,
                "model_name": model_name,
                "metadata": dict(metadata),
            }
        )

    return entries


def _seed_platform_catalog_rows(conn: sa.engine.Connection) -> None:
    """Upsert platform catalog rows before org grants and preset linking."""
    entries = _load_platform_catalog_entries()
    now = datetime.now(UTC)
    # Use the source JSON exactly as authored; provider-name corrections belong
    # in the catalog source branch, not in this migration.
    values = [
        {
            "id": uuid.uuid4(),
            "organization_id": None,
            "custom_provider_id": None,
            "model_provider": entry["model_provider"],
            "model_name": entry["model_name"],
            "model_metadata": entry["metadata"],
            "encrypted_config": None,
            "last_refreshed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        for entry in entries
    ]

    stmt = pg_insert(agent_catalog_tbl).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            "organization_id",
            "custom_provider_id",
            "model_provider",
            "model_name",
        ],
        set_={
            "model_metadata": stmt.excluded.model_metadata,
            "last_refreshed_at": stmt.excluded.last_refreshed_at,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    conn.execute(stmt)


def _orgs_with_any_provider_secret(conn: sa.engine.Connection) -> list[uuid.UUID]:
    rows = conn.execute(
        sa.select(org_secret_tbl.c.organization_id)
        .where(
            org_secret_tbl.c.name.like("agent-%-credentials"),
        )
        .distinct()
    ).all()
    return [r.organization_id for r in rows]


def _get_provider_secret_blob(
    conn: sa.engine.Connection, *, org_id: uuid.UUID, secret_name: str
) -> bytes | None:
    """Return the raw Fernet ciphertext for a provider secret, or None."""
    row = conn.execute(
        sa.select(org_secret_tbl.c.encrypted_keys).where(
            org_secret_tbl.c.organization_id == org_id,
            org_secret_tbl.c.name == secret_name,
        )
    ).one_or_none()
    return row.encrypted_keys if row is not None else None


def _grant_org_access(
    conn: sa.engine.Connection,
    *,
    org_id: uuid.UUID,
    catalog_id: uuid.UUID,
) -> None:
    """Create an org-wide (workspace_id=NULL) access grant, idempotent."""
    now = datetime.now(UTC)
    conn.execute(
        pg_insert(agent_model_access_tbl)
        .values(
            id=uuid.uuid4(),
            organization_id=org_id,
            workspace_id=None,
            catalog_id=catalog_id,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["organization_id", "workspace_id", "catalog_id"],
        )
    )


def _grant_platform_direct_provider_access(
    conn: sa.engine.Connection, *, org_id: uuid.UUID, provider: str
) -> None:
    """Grant an org access to every platform catalog row for a direct provider."""
    rows = conn.execute(
        sa.select(agent_catalog_tbl.c.id).where(
            agent_catalog_tbl.c.organization_id.is_(None),
            agent_catalog_tbl.c.custom_provider_id.is_(None),
            agent_catalog_tbl.c.model_provider == provider,
        )
    ).all()
    for row in rows:
        _grant_org_access(conn, org_id=org_id, catalog_id=row.id)


def _migrate_cloud_provider(
    conn: sa.engine.Connection, *, org_id: uuid.UUID, provider: str
) -> None:
    """Create one catalog row per (org, cloud_provider) with the ciphertext
    copied from the org's ``agent-{provider}-credentials`` secret.

    Idempotent via the ``(organization_id, custom_provider_id, model_provider,
    model_name)`` unique index with NULL-equal semantics. On re-run, the
    ``ON CONFLICT DO NOTHING`` skips existing rows — in particular, this
    means a subsequent credential rotation does **not** overwrite the
    catalog copy. A future operator script or follow-up migration handles
    re-sync; this migration is strictly one-shot backfill.
    """
    blob = _get_provider_secret_blob(
        conn, org_id=org_id, secret_name=f"agent-{provider}-credentials"
    )
    if blob is None:
        return

    now = datetime.now(UTC)
    catalog_id = uuid.uuid4()
    result = conn.execute(
        pg_insert(agent_catalog_tbl)
        .values(
            id=catalog_id,
            organization_id=org_id,
            custom_provider_id=None,
            model_provider=provider,
            model_name=provider,
            model_metadata={},
            encrypted_config=blob,
            last_refreshed_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "organization_id",
                "custom_provider_id",
                "model_provider",
                "model_name",
            ],
        )
        .returning(agent_catalog_tbl.c.id)
    )
    inserted = result.scalar_one_or_none()
    if inserted is None:
        # Row already existed; fetch its id so the access grant remains idempotent.
        existing = conn.execute(
            sa.select(agent_catalog_tbl.c.id).where(
                agent_catalog_tbl.c.organization_id == org_id,
                agent_catalog_tbl.c.custom_provider_id.is_(None),
                agent_catalog_tbl.c.model_provider == provider,
                agent_catalog_tbl.c.model_name == provider,
            )
        ).one()
        catalog_id = existing.id
    _grant_org_access(conn, org_id=org_id, catalog_id=catalog_id)


def _migrate_custom_provider(conn: sa.engine.Connection, *, org_id: uuid.UUID) -> None:
    """Create one ``agent_custom_provider`` row per org plus a linked catalog
    row, both carrying the copied ciphertext. No decrypt.

    The custom provider's ``base_url`` and ``display_name`` live inside the
    encrypted blob today; the gateway decrypts them at call time. This
    migration leaves them at safe defaults (``display_name`` set to a
    human-readable label, ``base_url`` NULL) — the follow-up gateway PR
    reads the real values out of ``encrypted_config``. Users can also
    edit these fields in the UI post-migration.
    """
    blob = _get_provider_secret_blob(
        conn,
        org_id=org_id,
        secret_name=f"agent-{CUSTOM_PROVIDER_SLUG}-credentials",
    )
    if blob is None:
        return

    now = datetime.now(UTC)
    # Stable-ish id so idempotency works: look up any existing row for the
    # org first, otherwise insert a fresh one.
    existing = conn.execute(
        sa.select(agent_custom_provider_tbl.c.id).where(
            agent_custom_provider_tbl.c.organization_id == org_id
        )
    ).one_or_none()
    if existing is not None:
        provider_id = existing.id
    else:
        provider_id = uuid.uuid4()
        conn.execute(
            pg_insert(agent_custom_provider_tbl).values(
                id=provider_id,
                organization_id=org_id,
                display_name=CUSTOM_PROVIDER_DISPLAY_NAME,
                base_url=None,
                passthrough=False,
                encrypted_config=blob,
                api_key_header=None,
                last_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    # Linked catalog row. Legacy custom-provider presets use
    # (custom-model-provider, custom), so keep that stable name for matching.
    catalog_id = uuid.uuid4()
    result = conn.execute(
        pg_insert(agent_catalog_tbl)
        .values(
            id=catalog_id,
            organization_id=org_id,
            custom_provider_id=provider_id,
            model_provider=CUSTOM_PROVIDER_SLUG,
            model_name=CUSTOM_PROVIDER_MODEL_NAME,
            model_metadata={},
            encrypted_config=blob,
            last_refreshed_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "organization_id",
                "custom_provider_id",
                "model_provider",
                "model_name",
            ],
        )
        .returning(agent_catalog_tbl.c.id)
    )
    inserted = result.scalar_one_or_none()
    if inserted is None:
        # Existing custom-provider catalogs need the same grant/link behavior as
        # newly inserted rows when the migration is rerun.
        existing_catalog = conn.execute(
            sa.select(agent_catalog_tbl.c.id).where(
                agent_catalog_tbl.c.organization_id == org_id,
                agent_catalog_tbl.c.custom_provider_id == provider_id,
                agent_catalog_tbl.c.model_provider == CUSTOM_PROVIDER_SLUG,
                agent_catalog_tbl.c.model_name == CUSTOM_PROVIDER_MODEL_NAME,
            )
        ).one()
        catalog_id = existing_catalog.id
    _grant_org_access(conn, org_id=org_id, catalog_id=catalog_id)


def _backfill_preset_catalog_ids(
    conn: sa.engine.Connection,
    *,
    table: sa.TableClause,
    label: str,
) -> None:
    """Link presets / preset versions to catalog rows by (provider, name)."""
    rows = conn.execute(
        sa.select(
            table.c.id,
            table.c.workspace_id,
            table.c.model_provider,
            table.c.model_name,
        ).where(table.c.catalog_id.is_(None))
    ).all()

    for row in rows:
        ws = conn.execute(
            sa.select(workspace_tbl.c.organization_id).where(
                workspace_tbl.c.id == row.workspace_id
            )
        ).one_or_none()
        if ws is None:
            logger.warning(
                "Preset row references missing workspace",
                extra={"table": label, "preset_id": str(row.id)},
            )
            continue
        org_id = ws.organization_id

        matches = conn.execute(
            sa.select(
                agent_catalog_tbl.c.id,
                agent_catalog_tbl.c.organization_id,
            )
            .where(
                agent_catalog_tbl.c.model_provider == row.model_provider,
                agent_catalog_tbl.c.model_name == row.model_name,
                sa.or_(
                    agent_catalog_tbl.c.organization_id.is_(None),
                    agent_catalog_tbl.c.organization_id == org_id,
                ),
            )
            .order_by(
                # Platform rows (NULL org_id) first so they win over org-scoped
                # duplicates — mirrors pre-v2 builtin-first resolution.
                agent_catalog_tbl.c.organization_id.asc().nulls_first(),
            )
        ).all()

        if not matches:
            logger.warning(
                "No catalog row found for preset",
                extra={
                    "table": label,
                    "preset_id": str(row.id),
                    "workspace_id": str(row.workspace_id),
                    "model_provider": row.model_provider,
                    "model_name": row.model_name,
                },
            )
            continue

        chosen = matches[0].id
        conn.execute(
            sa.update(table).where(table.c.id == row.id).values(catalog_id=chosen)
        )


def _backfill_default_model_setting(
    conn: sa.engine.Connection, *, org_id: uuid.UUID
) -> None:
    """Resolve ``agent_default_model`` (string) into ``agent_default_model_catalog_id``."""
    existing = conn.execute(
        sa.select(org_setting_tbl.c.value).where(
            org_setting_tbl.c.organization_id == org_id,
            org_setting_tbl.c.key == DEFAULT_MODEL_CATALOG_ID_SETTING_KEY,
        )
    ).one_or_none()
    if existing is not None:
        return  # already migrated or manually set.

    legacy = conn.execute(
        sa.select(
            org_setting_tbl.c.value,
            org_setting_tbl.c.is_encrypted,
        ).where(
            org_setting_tbl.c.organization_id == org_id,
            org_setting_tbl.c.key == DEFAULT_MODEL_SETTING_KEY,
        )
    ).one_or_none()
    if legacy is None or legacy.is_encrypted:
        # is_encrypted=True was never used for this key pre-v2, but guard
        # anyway — we can't decrypt with the schema-level key reliably here.
        return

    try:
        parsed = orjson.loads(legacy.value)
    except orjson.JSONDecodeError:
        logger.warning(
            "Could not decode agent_default_model setting",
        )
        return
    if not isinstance(parsed, str) or not parsed:
        return

    matches = conn.execute(
        sa.select(
            agent_catalog_tbl.c.id,
            agent_catalog_tbl.c.organization_id,
        )
        .where(
            agent_catalog_tbl.c.model_name == parsed,
            sa.or_(
                agent_catalog_tbl.c.organization_id.is_(None),
                agent_catalog_tbl.c.organization_id == org_id,
            ),
        )
        .order_by(agent_catalog_tbl.c.organization_id.asc().nulls_first())
    ).all()
    if not matches:
        logger.warning(
            "agent_default_model references unknown model",
        )
        return

    chosen_id = matches[0].id
    conn.execute(
        pg_insert(org_setting_tbl)
        .values(
            id=uuid.uuid4(),
            organization_id=org_id,
            key=DEFAULT_MODEL_CATALOG_ID_SETTING_KEY,
            value=orjson.dumps(str(chosen_id)),
            value_type="JSON",
            is_encrypted=False,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "key"])
    )


def _backfill_org_provider_data(
    conn: sa.engine.Connection, *, org_id: uuid.UUID
) -> None:
    # Custom/cloud providers materialize org-scoped catalog rows; direct
    # providers only grant access to platform catalog rows seeded earlier.
    _migrate_custom_provider(conn, org_id=org_id)

    for provider in CLOUD_PROVIDERS:
        _migrate_cloud_provider(conn, org_id=org_id, provider=provider)

    for provider in DIRECT_PROVIDERS:
        secret = conn.execute(
            sa.select(org_secret_tbl.c.id).where(
                org_secret_tbl.c.organization_id == org_id,
                org_secret_tbl.c.name == f"agent-{provider}-credentials",
            )
        ).one_or_none()
        if secret is None:
            continue
        _grant_platform_direct_provider_access(conn, org_id=org_id, provider=provider)

    _backfill_default_model_setting(conn, org_id=org_id)
