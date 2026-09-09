"""backfill assignments for membership dual writes

Revision ID: 4134d4ebdc69
Revises: c3a17be4d902
Create Date: 2026-09-08 17:43:26.780886

Phase one retains legacy membership reads and writes while RBAC mutations also
update membership in the same transaction. Backfill existing memberships before
enabling those writers. Derived readers and table removal are later releases.

Before cutting over, repeat the backfill after this bridge release is fully
deployed. Do not drop either table until the deployed app and its supported
rollback version no longer read or write it.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4134d4ebdc69"
down_revision: str | None = "c3a17be4d902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Backfill a workspace-editor assignment for every workspace membership that no
# assignment path already covers. Rows whose org lacks the system role are
# skipped here and caught by the guard below.
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
# org-wide assignment path. A workspace-scoped assignment does not count: org
# presence is the NULL-workspace slice, matching the legacy table exactly.
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
          AND  ura.workspace_id IS NULL
    )
    AND NOT EXISTS (
        SELECT 1
        FROM   group_role_assignment gra
        JOIN   group_member gm ON gm.group_id = gra.group_id
        WHERE  gm.user_id = om.user_id
          AND  gra.organization_id = om.organization_id
          AND  gra.workspace_id IS NULL
    )
    RETURNING 1
)
SELECT count(*) FROM inserted
"""

# Memberships still uncovered after the backfill. The backfill inner-joins
# `role`, so an org missing the system role silently skips its members, and
# they would vanish from the derived relation.
UNCOVERED_WORKSPACE_MEMBERSHIPS = """
SELECT count(*) AS uncovered
FROM   membership m
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
SELECT count(*) AS uncovered
FROM   organization_membership om
WHERE  NOT EXISTS (
    SELECT 1 FROM user_role_assignment ura
    WHERE  ura.user_id = om.user_id
      AND  ura.organization_id = om.organization_id
      AND  ura.workspace_id IS NULL
)
AND NOT EXISTS (
    SELECT 1
    FROM   group_role_assignment gra
    JOIN   group_member gm ON gm.group_id = gra.group_id
    WHERE  gm.user_id = om.user_id
      AND  gra.organization_id = om.organization_id
      AND  gra.workspace_id IS NULL
)
"""


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
    workspace_uncovered = connection.execute(
        sa.text(UNCOVERED_WORKSPACE_MEMBERSHIPS)
    ).scalar_one()
    org_uncovered = connection.execute(sa.text(UNCOVERED_ORG_MEMBERSHIPS)).scalar_one()
    if workspace_uncovered or org_uncovered:
        raise RuntimeError(
            "Refusing to derive membership from assignments: "
            f"{workspace_uncovered} workspace membership(s) and "
            f"{org_uncovered} organization membership(s) are not covered by any "
            "role assignment. Seed the 'workspace-editor' and "
            "'organization-member' system roles in the affected organizations, "
            "then retry."
        )


def upgrade() -> None:
    connection = op.get_bind()

    # Serialize the backfill with old membership writers. Ordinary SELECTs
    # remain available while the migration runs.
    connection.execute(
        sa.text(
            "LOCK TABLE membership, organization_membership IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    # The bridge keeps legacy reads. Cover existing rows before RBAC writers
    # start maintaining those rows from their surviving assignment paths.
    backfill_assignments(connection)
    assert_no_membership_dropped(connection)
    # Organization-level RBAC writers must mirror workspace membership without
    # bypassing RLS. Keep the existing workspace policy and allow the org-only
    # context to reach workspaces in that same organization.
    op.execute("""
        CREATE POLICY rls_policy_membership_org ON membership
        FOR ALL
        USING (
            NULLIF(current_setting('app.current_workspace_id', true), '') IS NULL
            AND EXISTS (
                SELECT 1 FROM workspace w
                WHERE w.id = membership.workspace_id
                  AND w.organization_id =
                      NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
        )
        WITH CHECK (
            NULLIF(current_setting('app.current_workspace_id', true), '') IS NULL
            AND EXISTS (
                SELECT 1 FROM workspace w
                WHERE w.id = membership.workspace_id
                  AND w.organization_id =
                      NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
        )
    """)


def downgrade() -> None:
    # Membership rows remain current for the old app. Backfilled grants stay.
    op.execute("DROP POLICY rls_policy_membership_org ON membership")
