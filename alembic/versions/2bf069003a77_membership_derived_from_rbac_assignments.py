"""membership derived from rbac assignments

Revision ID: 2bf069003a77
Revises: 44d7e75b6f4c
Create Date: 2026-09-02 13:35:40.252876

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection

from alembic import op
from tracecat.db.tenant_rls import (
    disable_assignment_split_table_rls,
    disable_org_optional_workspace_table_rls,
    enable_assignment_split_table_rls,
    enable_org_optional_workspace_table_rls,
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
# skips its members; the derived read path would lose their access.
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

# The legacy tables' rows, derived from the assignment graph. Used to reverse
# backfill the legacy tables so both representations agree after upgrade.
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

REPOPULATE_WORKSPACE_MEMBERSHIP = (
    f"INSERT INTO membership (user_id, workspace_id) {MEMBERSHIP_ROWS} "
    "ON CONFLICT DO NOTHING"
)
REPOPULATE_ORG_MEMBERSHIP = (
    "INSERT INTO organization_membership "
    f"(user_id, organization_id, created_at, updated_at) {ORG_MEMBERSHIP_ROWS} "
    "ON CONFLICT DO NOTHING"
)

# Workspace-leading indexes replace the dropped membership indexes; the existing
# unique constraints lead with user_id / group_id, so listing a workspace's
# members would otherwise scan the assignment tables.
WORKSPACE_INDEXES = (
    ("ix_user_role_assignment_workspace_id", "user_role_assignment", "workspace_id"),
    ("ix_group_role_assignment_workspace_id", "group_role_assignment", "workspace_id"),
    ("ix_group_member_group_id", "group_member", "group_id"),
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
            "Refusing to derive membership from assignments: "
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

    # 3. Reverse backfill: legacy tables keep their rows so N-1 pods, which
    # read and write them directly, keep working through the rolling upgrade.
    op.execute(REPOPULATE_ORG_MEMBERSHIP)
    op.execute(REPOPULATE_WORKSPACE_MEMBERSHIP)

    for name, table, column in WORKSPACE_INDEXES:
        op.create_index(name, table, [column], unique=False)


def downgrade() -> None:
    # The legacy tables were never dropped, so only the indexes and the RLS
    # swap are reversed. Backfilled assignments stay; they are valid grants.
    for name, table, _ in WORKSPACE_INDEXES:
        op.drop_index(name, table_name=table)

    for table in SPLIT_POLICY_TABLES:
        op.execute(disable_assignment_split_table_rls(table))
        op.execute(enable_org_optional_workspace_table_rls(table))
