"""force rls on dynamic workspace tables

Revision ID: b0debed4e0c6
Revises: 4a17af960e05
Create Date: 2026-11-05 17:01:41.966283

Dynamic workspace tables (Tables in `tables_<ws>`, case fields in
`custom_fields_<ws>`) already have RLS enabled with a workspace policy. Without
FORCE, the table owner skips that policy. The app role is going to own these
schemas, so the policy must apply to the owner too.

Forcing does not change results for the current app: off and shadow modes set
`app.rls_bypass=on` on every transaction, and superusers and BYPASSRLS roles
are never filtered.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b0debed4e0c6"
down_revision: str | None = "4a17af960e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Backslash escapes the LIKE wildcard so `tablesX...` schemas don't match.
DYNAMIC_SCHEMA_PATTERNS = (r"tables\_%", r"custom\_fields\_%")


def _dynamic_tables() -> list[tuple[str, str]]:
    """List every ordinary or partitioned table in dynamic workspace schemas."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT n.nspname, c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p')
              AND (n.nspname LIKE :tables_pattern
                   OR n.nspname LIKE :custom_fields_pattern)
            ORDER BY n.nspname, c.relname
            """
        ),
        {
            "tables_pattern": DYNAMIC_SCHEMA_PATTERNS[0],
            "custom_fields_pattern": DYNAMIC_SCHEMA_PATTERNS[1],
        },
    )
    return [(schema_name, table_name) for schema_name, table_name in rows.tuples()]


def _set_force_rls(*, force: bool) -> None:
    preparer = op.get_bind().dialect.identifier_preparer
    clause = "FORCE" if force else "NO FORCE"
    for schema_name, table_name in _dynamic_tables():
        qualified_table = (
            f"{preparer.quote_schema(schema_name)}.{preparer.quote(table_name)}"
        )
        op.execute(
            sa.text(f"ALTER TABLE {qualified_table} {clause} ROW LEVEL SECURITY")
        )


def upgrade() -> None:
    _set_force_rls(force=True)


def downgrade() -> None:
    _set_force_rls(force=False)
