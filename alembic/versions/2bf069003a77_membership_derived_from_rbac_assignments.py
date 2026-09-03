"""membership derived from rbac assignments

Revision ID: 2bf069003a77
Revises: 44d7e75b6f4c
Create Date: 2026-09-02 13:35:40.252876

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_assignment_split_table_rls,
    disable_org_optional_workspace_table_rls,
    disable_org_table_rls,
    disable_workspace_table_rls,
    enable_assignment_split_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "2bf069003a77"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

SPLIT_POLICY_TABLES = ("user_role_assignment", "group_role_assignment")

# Backfill a workspace-editor assignment for every workspace membership that no
# assignment path already covers. Rows whose org lacks the system role are
# skipped rather than failing the migration.
BACKFILL_WORKSPACE_ASSIGNMENTS = """
WITH inserted AS (
    INSERT INTO user_role_assignment (
        id, organization_id, user_id, workspace_id, role_id
    )
    SELECT gen_random_uuid(), w.organization_id, m.user_id, m.workspace_id, r.id
    FROM   membership m
    JOIN   workspace w ON w.id = m.workspace_id
    JOIN   role r
      ON   r.organization_id = w.organization_id
     AND   r.slug = 'workspace-editor'
    WHERE  NOT EXISTS (
        SELECT 1 FROM user_role_assignment ura
        WHERE  ura.user_id = m.user_id
          AND  ura.workspace_id = m.workspace_id
    )
    AND NOT EXISTS (
        SELECT 1
        FROM   group_role_assignment gra
        JOIN   group_member gm ON gm.group_id = gra.group_id
        WHERE  gm.user_id = m.user_id
          AND  gra.workspace_id = m.workspace_id
    )
    RETURNING 1
)
SELECT count(*) FROM inserted
"""

# Backfill an organization-member assignment for every org membership with no
# assignment at all in that organization.
BACKFILL_ORG_ASSIGNMENTS = """
WITH inserted AS (
    INSERT INTO user_role_assignment (
        id, organization_id, user_id, workspace_id, role_id
    )
    SELECT gen_random_uuid(), om.organization_id, om.user_id, NULL, r.id
    FROM   organization_membership om
    JOIN   role r
      ON   r.organization_id = om.organization_id
     AND   r.slug = 'organization-member'
    WHERE  NOT EXISTS (
        SELECT 1 FROM user_role_assignment ura
        WHERE  ura.user_id = om.user_id
          AND  ura.organization_id = om.organization_id
    )
    AND NOT EXISTS (
        SELECT 1
        FROM   group_role_assignment gra
        JOIN   group_member gm ON gm.group_id = gra.group_id
        WHERE  gm.user_id = om.user_id
          AND  gra.organization_id = om.organization_id
    )
    RETURNING 1
)
SELECT count(*) FROM inserted
"""

# Workspace memberships still uncovered by any assignment path after backfill.
# The backfill inner-joins `role`, so an org missing the system role silently
# skips its members; dropping the table would lose their access permanently.
UNCOVERED_WORKSPACE_MEMBERSHIPS = """
SELECT count(*) AS uncovered,
       coalesce(array_agg(DISTINCT w.organization_id), '{}') AS organization_ids
FROM   membership m
JOIN   workspace w ON w.id = m.workspace_id
WHERE  NOT EXISTS (
    SELECT 1 FROM user_role_assignment ura
    WHERE  ura.user_id = m.user_id
      AND  ura.workspace_id = m.workspace_id
)
AND NOT EXISTS (
    SELECT 1
    FROM   group_role_assignment gra
    JOIN   group_member gm ON gm.group_id = gra.group_id
    WHERE  gm.user_id = m.user_id
      AND  gra.workspace_id = m.workspace_id
)
"""

UNCOVERED_ORG_MEMBERSHIPS = """
SELECT count(*) AS uncovered,
       coalesce(array_agg(DISTINCT om.organization_id), '{}') AS organization_ids
FROM   organization_membership om
WHERE  NOT EXISTS (
    SELECT 1 FROM user_role_assignment ura
    WHERE  ura.user_id = om.user_id
      AND  ura.organization_id = om.organization_id
)
AND NOT EXISTS (
    SELECT 1
    FROM   group_role_assignment gra
    JOIN   group_member gm ON gm.group_id = gra.group_id
    WHERE  gm.user_id = om.user_id
      AND  gra.organization_id = om.organization_id
)
"""

# The old tables' rows, derived from the assignment graph. Shared by the
# compatibility views on upgrade and the repopulated tables on downgrade.
MEMBERSHIP_ROWS = """
SELECT DISTINCT user_id, workspace_id
FROM (
  SELECT user_id, workspace_id
  FROM   user_role_assignment WHERE workspace_id IS NOT NULL
  UNION ALL
  SELECT gm.user_id, gra.workspace_id
  FROM   group_role_assignment gra JOIN group_member gm ON gm.group_id = gra.group_id
  WHERE  gra.workspace_id IS NOT NULL
) w
"""

ORG_MEMBERSHIP_ROWS = """
SELECT user_id, organization_id, min(assigned_at) AS created_at,
       max(assigned_at) AS updated_at
FROM (
  SELECT user_id, organization_id, assigned_at FROM user_role_assignment
  UNION ALL
  SELECT gm.user_id, gra.organization_id, gra.assigned_at
  FROM   group_role_assignment gra JOIN group_member gm ON gm.group_id = gra.group_id
) o
GROUP BY user_id, organization_id
"""

# Compatibility views keep the dropped table names readable while old API pods
# still query them during a rolling upgrade; writes to them fail in that window.
# security_invoker (PG 15+) applies the caller's RLS on the assignment tables.
# TODO: a later contract migration drops both views once no old pod remains.
CREATE_MEMBERSHIP_VIEW = (
    f"CREATE VIEW membership WITH (security_invoker = true) AS {MEMBERSHIP_ROWS}"
)
CREATE_ORG_MEMBERSHIP_VIEW = (
    "CREATE VIEW organization_membership WITH (security_invoker = true) AS "
    f"{ORG_MEMBERSHIP_ROWS}"
)

REPOPULATE_WORKSPACE_MEMBERSHIP = (
    f"INSERT INTO membership (user_id, workspace_id) {MEMBERSHIP_ROWS} "
    "ON CONFLICT DO NOTHING"
)
REPOPULATE_ORG_MEMBERSHIP = (
    "INSERT INTO organization_membership "
    f"(user_id, organization_id, created_at, updated_at) {ORG_MEMBERSHIP_ROWS} "
    "ON CONFLICT DO NOTHING"
)

DROP_COMPAT_VIEWS = (
    "DROP VIEW IF EXISTS membership",
    "DROP VIEW IF EXISTS organization_membership",
)


def backfill_assignments(connection: Connection) -> tuple[int, int]:
    """Backfill assignments for every legacy membership, returning the counts."""
    workspace_backfilled = connection.execute(
        sa.text(BACKFILL_WORKSPACE_ASSIGNMENTS)
    ).scalar_one()
    org_backfilled = connection.execute(sa.text(BACKFILL_ORG_ASSIGNMENTS)).scalar_one()
    logger.info(
        "membership backfill: %s workspace, %s org assignments",
        workspace_backfilled,
        org_backfilled,
    )
    return workspace_backfilled, org_backfilled


def assert_no_membership_dropped(connection: Connection) -> None:
    """Fail the migration if any legacy membership row lacks an assignment path."""
    workspace_row = connection.execute(sa.text(UNCOVERED_WORKSPACE_MEMBERSHIPS)).one()
    org_row = connection.execute(sa.text(UNCOVERED_ORG_MEMBERSHIPS)).one()
    if workspace_row.uncovered or org_row.uncovered:
        affected = sorted(
            {str(org_id) for org_id in workspace_row.organization_ids}
            | {str(org_id) for org_id in org_row.organization_ids}
        )
        raise RuntimeError(
            "Refusing to drop membership tables: "
            f"{workspace_row.uncovered} workspace membership(s) and "
            f"{org_row.uncovered} organization membership(s) are not covered by "
            "any role assignment. Affected organization_ids: "
            f"{', '.join(affected)}. Seed the 'workspace-editor' and "
            "'organization-member' system roles in these organizations, then retry."
        )


def upgrade() -> None:
    connection = op.get_bind()

    # 1. Backfill assignments so no existing member loses access, then verify.
    backfill_assignments(connection)
    assert_no_membership_dropped(connection)

    # 2. Assignments gain an org-wide read policy so org-presence queries see
    # other-workspace rows; writes stay pinned to the session's workspace.
    for table in SPLIT_POLICY_TABLES:
        op.execute(disable_org_optional_workspace_table_rls(table))
        op.execute(enable_assignment_split_table_rls(table))

    # 3. Drop the legacy membership tables.
    op.execute(disable_workspace_table_rls("membership"))
    op.execute(disable_org_table_rls("organization_membership"))
    op.drop_index("ix_membership_workspace_id", table_name="membership")
    op.drop_index("ix_membership_workspace_user", table_name="membership")
    op.drop_table("membership")
    op.drop_index("ix_org_membership_org_id", table_name="organization_membership")
    op.drop_table("organization_membership")

    # 4. Recreate the names as read-only views so old pods survive the rollout.
    op.execute(CREATE_MEMBERSHIP_VIEW)
    op.execute(CREATE_ORG_MEMBERSHIP_VIEW)


def downgrade() -> None:
    # The views own these names until dropped; the tables below reclaim them.
    for statement in DROP_COMPAT_VIEWS:
        op.execute(statement)

    op.create_table(
        "membership",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name="fk_membership_user_id_user"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name="fk_membership_workspace_id_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "workspace_id", name="pk_membership"),
    )
    op.create_index(
        "ix_membership_workspace_id", "membership", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_membership_workspace_user",
        "membership",
        ["workspace_id", "user_id"],
        unique=False,
    )

    op.create_table(
        "organization_membership",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_membership_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_organization_membership_user_id_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "organization_id", name="pk_organization_membership"
        ),
    )
    op.create_index(
        "ix_org_membership_org_id",
        "organization_membership",
        ["organization_id"],
        unique=False,
    )

    op.execute(REPOPULATE_ORG_MEMBERSHIP)
    op.execute(REPOPULATE_WORKSPACE_MEMBERSHIP)

    op.execute(enable_workspace_table_rls("membership"))
    op.execute(enable_org_table_rls("organization_membership"))

    for table in SPLIT_POLICY_TABLES:
        op.execute(disable_assignment_split_table_rls(table))
        op.execute(enable_org_optional_workspace_table_rls(table))
