"""repair spm finding control identifiers

Revision ID: a46c2f1d9b87
Revises: ed7b7d97ede5
Create Date: 2026-04-28 00:00:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a46c2f1d9b87"
down_revision: str | None = "ed7b7d97ede5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTROL_IDS_BY_KEY: dict[str, str] = {
    "claude.additional_directory.approved": "7725b340-3edc-4ef8-a2ea-63c419b9a9ee",
    "claude.hook.approved": "c08bb549-e616-41ce-a414-e2c13aa98a33",
    "claude.hook.risk_ok": "2f543556-e7b4-486f-817b-724694b81830",
    "claude.instruction_file.external_indicators_reputation_ok": (
        "8e8445fd-9858-41c5-af2a-d16dd8424cb6"
    ),
    "claude.instruction_file.language_english": "87c10d25-c2c0-4803-82c0-19da6255b5e7",
    "claude.instruction_file.obfuscation_absent": (
        "4fd32453-138e-4273-8501-bf4809eb7adf"
    ),
    "claude.mcp_server.approved": "7dca8397-056a-4cc7-a4a6-3fef782b21a2",
    "claude.mcp_server.reputation_ok": "6030d9e9-8c58-4068-89fa-74a4ffbaf5c1",
    "claude.mcp_server.vulnerability_ok": "7c2f1c07-db25-4ffc-b4fd-0e0eff459ca4",
    "claude.permission_config.approved": "2bc49aae-22f1-4de3-b582-48fea567e792",
    "claude.sandbox_config.approved": "7533af3c-a9f8-45ce-a891-b3ed17c79015",
    "claude.skill.approved": "2318df58-a153-4a71-ae52-ddee6270d976",
    "claude.skill.risk_ok": "d304640f-3bdd-4669-b189-35e809525277",
    "claude.trusted_directory.approved": "834d11af-3e75-49b5-aff0-1441d0d5d616",
}
UNKNOWN_CONTROL_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "tracecat.spm.unknown-control"
)
UNIQUE_CONSTRAINT = "uq_spm_finding_endpoint_asset_control"


def _spm_finding_columns() -> dict[str, sa.engine.interfaces.ReflectedColumn]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("spm_finding"):
        return {}
    return {column["name"]: column for column in inspector.get_columns("spm_finding")}


def _spm_finding_unique_constraints() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("spm_finding"):
        return set()
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("spm_finding")
        if constraint["name"] is not None
    }


def _is_uuid_type(column: sa.engine.interfaces.ReflectedColumn) -> bool:
    column_type = column["type"]
    return (
        isinstance(column_type, postgresql.UUID) or str(column_type).upper() == "UUID"
    )


def _uuid_for_control_key(control_key: str) -> str:
    if control_id := CONTROL_IDS_BY_KEY.get(control_key):
        return control_id
    return str(uuid.uuid5(UNKNOWN_CONTROL_NAMESPACE, control_key))


def _backfill_control_keys_from_uuid_ids() -> None:
    bind = op.get_bind()
    for control_key, control_id in CONTROL_IDS_BY_KEY.items():
        bind.execute(
            sa.text(
                """
                UPDATE spm_finding
                SET control_key = :control_key
                WHERE control_key IS NULL
                  AND control_id = CAST(:control_id AS uuid)
                """
            ),
            {"control_key": control_key, "control_id": control_id},
        )
    bind.execute(
        sa.text(
            """
            UPDATE spm_finding
            SET control_key = control_id::text
            WHERE control_key IS NULL
            """
        )
    )


def _backfill_uuid_ids_from_control_keys() -> None:
    bind = op.get_bind()
    control_keys = bind.execute(
        sa.text(
            """
            SELECT DISTINCT control_key
            FROM spm_finding
            WHERE control_key IS NOT NULL
            """
        )
    ).scalars()
    for control_key in control_keys:
        bind.execute(
            sa.text(
                """
                UPDATE spm_finding
                SET control_id_uuid = CAST(:control_id AS uuid)
                WHERE control_key = :control_key
                """
            ),
            {
                "control_id": _uuid_for_control_key(control_key),
                "control_key": control_key,
            },
        )


def upgrade() -> None:
    columns = _spm_finding_columns()
    if not columns:
        return

    control_key_missing = "control_key" not in columns
    if control_key_missing:
        op.add_column(
            "spm_finding",
            sa.Column("control_key", sa.String(length=255), nullable=True),
        )
        columns = _spm_finding_columns()

    if _is_uuid_type(columns["control_id"]):
        control_key_nullable = columns["control_key"]["nullable"]
        _backfill_control_keys_from_uuid_ids()
        if control_key_missing or control_key_nullable:
            op.alter_column(
                "spm_finding",
                "control_key",
                existing_type=sa.String(length=255),
                nullable=False,
            )
        return

    op.execute(
        sa.text(
            """
            UPDATE spm_finding
            SET control_key = control_id
            WHERE control_key IS NULL
            """
        )
    )
    if "control_id_uuid" not in columns:
        op.add_column(
            "spm_finding",
            sa.Column("control_id_uuid", postgresql.UUID(), nullable=True),
        )
    _backfill_uuid_ids_from_control_keys()

    missing_control_ids = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT COUNT(*)
            FROM spm_finding
            WHERE control_id_uuid IS NULL
            """
            )
        )
        .scalar_one()
    )
    if missing_control_ids:
        raise RuntimeError("Unable to backfill SPM finding control UUIDs.")

    if UNIQUE_CONSTRAINT in _spm_finding_unique_constraints():
        op.drop_constraint(UNIQUE_CONSTRAINT, "spm_finding", type_="unique")
    op.drop_column("spm_finding", "control_id")
    op.alter_column(
        "spm_finding",
        "control_id_uuid",
        new_column_name="control_id",
        existing_type=postgresql.UUID(),
        nullable=False,
    )
    op.alter_column(
        "spm_finding",
        "control_key",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_unique_constraint(
        UNIQUE_CONSTRAINT,
        "spm_finding",
        ["organization_id", "endpoint_id", "asset_id", "control_id"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrading a repaired SPM finding control_id column is not safe; "
        "restore the database from backup before rolling back this migration."
    )
