"""Enable tenant RLS on tables without a tenant column

Revision ID: c10b1e9299db
Revises: 11e685c62cfd
Create Date: 2026-11-16 12:00:00.000000

Link and child tables such as ``case_tag_link`` and ``table_column`` hold
tenant data but carry no ``workspace_id`` or ``organization_id``. Their
policies admit a row only when every parent it references belongs to the
current workspace (or organization for ``role_scope`` and ``ownership``).
Policy-only: no columns, no data changes, and the previous app version runs
unchanged. Downgrade drops the policies and disables RLS again.
"""

from collections.abc import Sequence

from alembic import op
from tracecat.db.tenant_rls import (
    disable_parent_scoped_table_rls,
    enable_parent_scoped_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "c10b1e9299db"
down_revision: str | None = "11e685c62cfd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "case_attachment",
    "case_dropdown_option",
    "case_dropdown_value",
    "case_tag_link",
    "workflow_tag_link",
    "table_column",
    "role_scope",
    "ownership",
)


def upgrade() -> None:
    # ENABLE ROW LEVEL SECURITY locks each table; fail fast rather than queue
    # behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table in TABLES:
        op.execute(enable_parent_scoped_table_rls(table))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table in reversed(TABLES):
        op.execute(disable_parent_scoped_table_rls(table))
