"""apply model a rls to dynamic workspace schemas

Revision ID: b5fc4168fe22
Revises: c76f9b01fad7
Create Date: 2026-02-27 11:43:07.059399

"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID

# revision identifiers, used by Alembic.
revision: str = "b5fc4168fe22"
down_revision: str | None = "c76f9b01fad7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERNAL_TENANT_COLUMN = "__tc_workspace_id"
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


def _ensure_tenant_column(
    *,
    schema_name: str,
    table_name: str,
    workspace_id: UUID,
    preparer: sa.sql.compiler.IdentifierPreparer,
) -> None:
    """Add/backfill the internal tenant column on a physical dynamic table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    qualified_table = _qualified_table(preparer, schema_name, table_name)

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
