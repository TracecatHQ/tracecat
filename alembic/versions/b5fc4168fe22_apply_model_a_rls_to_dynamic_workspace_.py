"""apply model a rls to dynamic workspace schemas

Revision ID: b5fc4168fe22
Revises: 6171727be56a
Create Date: 2026-02-27 11:43:07.059399

"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID

# revision identifiers, used by Alembic.
revision: str = "b5fc4168fe22"
down_revision: str | None = "6171727be56a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERNAL_TENANT_COLUMN = "__tc_workspace_id"
LEGACY_TENANT_COLUMN_PREFIX = "migrated_tc_workspace_id"
DYNAMIC_WORKSPACE_RLS_POLICY = "rls_policy_dynamic_workspace"
RLS_WORKSPACE_VAR = "app.current_workspace_id"
RLS_BYPASS_VAR = "app.rls_bypass"
RLS_BYPASS_ON = "on"
DYNAMIC_WORKSPACE_SCHEMA_PREFIXES = ("tables_", "custom_fields_")


def _workspace_id_from_schema(schema_name: str) -> UUID | None:
    """Resolve workspace UUID from a dynamic workspace schema name."""
    for prefix in DYNAMIC_WORKSPACE_SCHEMA_PREFIXES:
        if not schema_name.startswith(prefix):
            continue
        short_workspace_id = schema_name.removeprefix(prefix)
        try:
            return WorkspaceUUID.new(short_workspace_id)
        except ValueError:
            return None
    return None


def _qualified_table(
    preparer: sa.sql.compiler.IdentifierPreparer, schema_name: str, table_name: str
) -> str:
    """Return a properly quoted schema-qualified table reference."""
    return f"{preparer.quote_schema(schema_name)}.{preparer.quote(table_name)}"


def _workspace_dynamic_tables() -> list[tuple[str, str, UUID]]:
    """Collect all physical tables in workspace-scoped dynamic schemas."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables: list[tuple[str, str, UUID]] = []
    for schema_name in sorted(inspector.get_schema_names()):
        workspace_id = _workspace_id_from_schema(schema_name)
        if workspace_id is None:
            continue
        for table_name in sorted(inspector.get_table_names(schema=schema_name)):
            tables.append((schema_name, table_name, workspace_id))
    return tables


def _policy_expression() -> str:
    """Build the RLS tenant policy expression for dynamic workspace tables."""
    return (
        f"current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}' "
        f'OR "{INTERNAL_TENANT_COLUMN}" = '
        f"NULLIF(current_setting('{RLS_WORKSPACE_VAR}', true), '')::uuid"
    )


def _next_legacy_tenant_column_name(existing_column_names: set[str]) -> str:
    """Generate a non-conflicting rename target for legacy tenant collisions."""
    candidate = LEGACY_TENANT_COLUMN_PREFIX
    suffix = 1
    while candidate in existing_column_names:
        candidate = f"{LEGACY_TENANT_COLUMN_PREFIX}_{suffix}"
        suffix += 1
    return candidate


def _rename_table_column_metadata(
    *,
    workspace_id: UUID,
    table_name: str,
    old_name: str,
    new_name: str,
) -> None:
    """Rename matching table-column metadata when a physical column moves."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not (
        inspector.has_table("tables", schema="public")
        and inspector.has_table("table_column", schema="public")
    ):
        return

    bind.execute(
        sa.text(
            """
            UPDATE table_column AS tc
            SET name = :new_name
            FROM tables AS t
            WHERE tc.table_id = t.id
              AND t.workspace_id = :workspace_id
              AND t.name = :table_name
              AND tc.name = :old_name
            """
        ),
        {
            "workspace_id": str(workspace_id),
            "table_name": table_name,
            "old_name": old_name,
            "new_name": new_name,
        },
    )


def _rename_case_field_schema_key(
    *,
    workspace_id: UUID,
    old_name: str,
    new_name: str,
) -> None:
    """Rename a case-field schema key when the physical column moves."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("case_field", schema="public"):
        return

    case_field_table = sa.table(
        "case_field",
        sa.column("workspace_id", sa.UUID()),
        sa.column("schema", JSONB),
    )
    current_schema = bind.execute(
        sa.select(case_field_table.c.schema).where(
            case_field_table.c.workspace_id == workspace_id
        )
    ).scalar_one_or_none()
    if not isinstance(current_schema, dict) or old_name not in current_schema:
        return

    updated_schema = dict(current_schema)
    updated_schema[new_name] = updated_schema.pop(old_name)
    bind.execute(
        sa.update(case_field_table)
        .where(case_field_table.c.workspace_id == workspace_id)
        .values(schema=updated_schema)
    )


def _rename_legacy_tenant_column(
    *,
    schema_name: str,
    table_name: str,
    workspace_id: UUID,
    preparer: sa.sql.compiler.IdentifierPreparer,
) -> None:
    """Rename any pre-existing user column that collides with the tenant column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name, schema=schema_name)
    column_names = {column["name"] for column in columns}
    if INTERNAL_TENANT_COLUMN not in column_names:
        return

    qualified_table = _qualified_table(preparer, schema_name, table_name)
    legacy_column_name = _next_legacy_tenant_column_name(column_names)
    op.execute(
        sa.text(
            f"""
            ALTER TABLE {qualified_table}
            RENAME COLUMN "{INTERNAL_TENANT_COLUMN}" TO "{legacy_column_name}"
            """
        )
    )

    if schema_name.startswith("tables_"):
        _rename_table_column_metadata(
            workspace_id=workspace_id,
            table_name=table_name,
            old_name=INTERNAL_TENANT_COLUMN,
            new_name=legacy_column_name,
        )
    elif schema_name.startswith("custom_fields_") and table_name == "case_fields":
        _rename_case_field_schema_key(
            workspace_id=workspace_id,
            old_name=INTERNAL_TENANT_COLUMN,
            new_name=legacy_column_name,
        )


def _ensure_tenant_column(
    *,
    schema_name: str,
    table_name: str,
    workspace_id: UUID,
    preparer: sa.sql.compiler.IdentifierPreparer,
) -> None:
    """Add/backfill the internal tenant column on a physical dynamic table."""
    qualified_table = _qualified_table(preparer, schema_name, table_name)

    _rename_legacy_tenant_column(
        schema_name=schema_name,
        table_name=table_name,
        workspace_id=workspace_id,
        preparer=preparer,
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_tenant_column = any(
        column["name"] == INTERNAL_TENANT_COLUMN
        for column in inspector.get_columns(table_name, schema=schema_name)
    )
    if not has_tenant_column:
        op.execute(
            sa.text(
                f"""
                ALTER TABLE {qualified_table}
                ADD COLUMN "{INTERNAL_TENANT_COLUMN}" UUID
                """
            )
        )

    bind.execute(
        sa.text(
            f"""
            UPDATE {qualified_table}
            SET "{INTERNAL_TENANT_COLUMN}" = :workspace_id
            WHERE "{INTERNAL_TENANT_COLUMN}" IS NULL
            """
        ),
        {"workspace_id": str(workspace_id)},
    )

    op.execute(
        sa.text(
            f"""
            ALTER TABLE {qualified_table}
            ALTER COLUMN "{INTERNAL_TENANT_COLUMN}"
            SET DEFAULT '{workspace_id}'::uuid
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            ALTER TABLE {qualified_table}
            ALTER COLUMN "{INTERNAL_TENANT_COLUMN}"
            SET NOT NULL
            """
        )
    )


def _enable_table_rls(
    *, schema_name: str, table_name: str, preparer: sa.sql.compiler.IdentifierPreparer
) -> None:
    """Enable RLS policy for a physical dynamic table."""
    qualified_table = _qualified_table(preparer, schema_name, table_name)
    policy_expr = _policy_expression()

    op.execute(sa.text(f"ALTER TABLE {qualified_table} ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            DROP POLICY IF EXISTS {DYNAMIC_WORKSPACE_RLS_POLICY}
            ON {qualified_table}
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {DYNAMIC_WORKSPACE_RLS_POLICY} ON {qualified_table}
                FOR ALL
                USING ({policy_expr})
                WITH CHECK ({policy_expr})
            """
        )
    )


def _disable_table_rls(
    *, schema_name: str, table_name: str, preparer: sa.sql.compiler.IdentifierPreparer
) -> None:
    """Disable RLS policy for a physical dynamic table."""
    qualified_table = _qualified_table(preparer, schema_name, table_name)
    op.execute(
        sa.text(
            f"""
            DROP POLICY IF EXISTS {DYNAMIC_WORKSPACE_RLS_POLICY}
            ON {qualified_table}
            """
        )
    )
    op.execute(sa.text(f"ALTER TABLE {qualified_table} DISABLE ROW LEVEL SECURITY"))


def upgrade() -> None:
    """Apply workspace-tenant column + RLS to dynamic workspace schemas."""
    bind = op.get_bind()
    preparer = sa.sql.compiler.IdentifierPreparer(bind.dialect)

    for schema_name, table_name, workspace_id in _workspace_dynamic_tables():
        _ensure_tenant_column(
            schema_name=schema_name,
            table_name=table_name,
            workspace_id=workspace_id,
            preparer=preparer,
        )
        _enable_table_rls(
            schema_name=schema_name,
            table_name=table_name,
            preparer=preparer,
        )


def downgrade() -> None:
    """Rollback workspace-tenant column + RLS on dynamic workspace schemas."""
    bind = op.get_bind()
    preparer = sa.sql.compiler.IdentifierPreparer(bind.dialect)

    for schema_name, table_name, _ in _workspace_dynamic_tables():
        _disable_table_rls(
            schema_name=schema_name,
            table_name=table_name,
            preparer=preparer,
        )
        qualified_table = _qualified_table(preparer, schema_name, table_name)
        op.execute(
            sa.text(
                f"""
                ALTER TABLE {qualified_table}
                DROP COLUMN IF EXISTS "{INTERNAL_TENANT_COLUMN}"
                """
            )
        )
