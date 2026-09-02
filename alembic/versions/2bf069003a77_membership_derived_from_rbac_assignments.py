"""membership derived from rbac assignments

Revision ID: 2bf069003a77
Revises: 44d7e75b6f4c
Create Date: 2026-09-02 13:35:40.252876

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_org_table_rls,
    disable_workspace_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "2bf069003a77"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECLASSIFIED_TABLES = ("user_role_assignment", "group_role_assignment")

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

# Repopulate the dropped tables from the assignment graph on downgrade.
REPOPULATE_ORG_MEMBERSHIP = """
INSERT INTO organization_membership (user_id, organization_id)
SELECT DISTINCT user_id, organization_id
FROM (
  SELECT user_id, organization_id FROM user_role_assignment
  UNION ALL
  SELECT gm.user_id, gra.organization_id
  FROM   group_role_assignment gra JOIN group_member gm ON gm.group_id = gra.group_id
) o
ON CONFLICT DO NOTHING
"""

REPOPULATE_WORKSPACE_MEMBERSHIP = """
INSERT INTO membership (user_id, workspace_id)
SELECT DISTINCT user_id, workspace_id
FROM (
  SELECT user_id, workspace_id
  FROM   user_role_assignment WHERE workspace_id IS NOT NULL
  UNION ALL
  SELECT gm.user_id, gra.workspace_id
  FROM   group_role_assignment gra JOIN group_member gm ON gm.group_id = gra.group_id
  WHERE  gra.workspace_id IS NOT NULL
) w
ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    connection = op.get_bind()

    # 1. Backfill assignments so no existing member loses access.
    workspace_backfilled = connection.execute(
        sa.text(BACKFILL_WORKSPACE_ASSIGNMENTS)
    ).scalar_one()
    org_backfilled = connection.execute(sa.text(BACKFILL_ORG_ASSIGNMENTS)).scalar_one()
    connection.execute(
        sa.text(
            "DO $$ BEGIN RAISE NOTICE "
            "'membership backfill: % workspace, % org assignments', "
            f"{workspace_backfilled}, {org_backfilled}; END $$"
        )
    )

    # 2. Assignments become plain org-scoped: the optional-workspace clause hid
    # other-workspace rows from the view's org-presence reads.
    for table in RECLASSIFIED_TABLES:
        op.execute(disable_org_optional_workspace_table_rls(table))
        op.execute(enable_org_table_rls(table))

    # 3. Drop the legacy membership tables.
    op.execute(disable_workspace_table_rls("membership"))
    op.execute(disable_org_table_rls("organization_membership"))
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

    for table in RECLASSIFIED_TABLES:
        op.execute(disable_org_table_rls(table))
        op.execute(enable_org_optional_workspace_table_rls(table))
