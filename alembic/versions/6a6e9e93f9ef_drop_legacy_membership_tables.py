"""drop legacy membership tables

Revision ID: 6a6e9e93f9ef
Revises: 4134d4ebdc69
Create Date: 2026-09-08 17:49:27.287739

Nothing reads or writes `membership` and `organization_membership` any more
(see revision 4134d4ebdc69). The backfill and guard run once more to cover
rows written by the previous app version during its rollout, then both tables
are dropped.

Downgrade recreates them from the assignment graph: every row they held comes
back, plus rows for users who already held an assignment without one.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import enable_org_table_rls, enable_workspace_table_rls

# revision identifiers, used by Alembic.
revision: str = "6a6e9e93f9ef"
down_revision: str | None = "4134d4ebdc69"
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

# Downgrade repopulates the tables from the same derivation the ORM reads.
REPOPULATE_WORKSPACE_MEMBERSHIP = """
INSERT INTO membership (user_id, workspace_id)
SELECT DISTINCT user_id, workspace_id
FROM (
    SELECT user_id, workspace_id FROM user_role_assignment
    UNION ALL
    SELECT gm.user_id, gra.workspace_id
    FROM   group_role_assignment gra
    JOIN   group_member gm ON gm.group_id = gra.group_id
) AS paths
WHERE  workspace_id IS NOT NULL
"""

REPOPULATE_ORG_MEMBERSHIP = """
INSERT INTO organization_membership (user_id, organization_id)
SELECT DISTINCT user_id, organization_id
FROM (
    SELECT user_id, organization_id, workspace_id FROM user_role_assignment
    UNION ALL
    SELECT gm.user_id, gra.organization_id, gra.workspace_id
    FROM   group_role_assignment gra
    JOIN   group_member gm ON gm.group_id = gra.group_id
) AS paths
WHERE  workspace_id IS NULL
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

    # The previous app version may have written membership rows during its
    # rollout; cover them before the tables go.
    backfill_assignments(connection)
    assert_no_membership_dropped(connection)

    # DROP TABLE removes the tables' RLS policies with them.
    op.drop_index("ix_membership_workspace_id", table_name="membership")
    op.drop_index("ix_membership_workspace_user", table_name="membership")
    op.drop_table("membership")
    op.drop_index("ix_org_membership_org_id", table_name="organization_membership")
    op.drop_table("organization_membership")


def downgrade() -> None:
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
        "ix_membership_workspace_user",
        "membership",
        ["workspace_id", "user_id"],
        unique=False,
    )
    op.create_index(
        "ix_membership_workspace_id", "membership", ["workspace_id"], unique=False
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

    op.execute(REPOPULATE_WORKSPACE_MEMBERSHIP)
    op.execute(REPOPULATE_ORG_MEMBERSHIP)

    op.execute(enable_workspace_table_rls("membership"))
    op.execute(enable_org_table_rls("organization_membership"))
