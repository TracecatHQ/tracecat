"""membership derived from role assignments

Revision ID: 4134d4ebdc69
Revises: 526f867f6a75
Create Date: 2026-09-08 17:43:26.780886

Membership becomes a read-only relation the ORM derives from the role-assignment
tables (see tracecat.db.models). The derived readers cover every row the writers
produce, so this revision changes no data and no schema: it only counts legacy
rows no assignment path covers. Such a row is a member whose role was removed,
which the derived relation already treats as absent, so it is reported and not
repaired. The `membership` and `organization_membership` tables stay in place
and are still written; the stacked follow-up revision drops them.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4134d4ebdc69"
down_revision: str | None = "526f867f6a75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Legacy rows the derived relation will not show, because neither a direct nor
# a group assignment path covers them.
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

# Org presence is the NULL-workspace slice, matching the legacy table exactly:
# a workspace-scoped assignment does not count.
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


def count_uncovered_memberships(connection: Connection) -> tuple[int, int]:
    """Count legacy membership rows no assignment path covers."""
    workspace_uncovered = connection.execute(
        sa.text(UNCOVERED_WORKSPACE_MEMBERSHIPS)
    ).scalar_one()
    org_uncovered = connection.execute(sa.text(UNCOVERED_ORG_MEMBERSHIPS)).scalar_one()
    return workspace_uncovered, org_uncovered


def upgrade() -> None:
    connection = op.get_bind()

    workspace_uncovered, org_uncovered = count_uncovered_memberships(connection)
    if workspace_uncovered or org_uncovered:
        # Counts only: never log user or workspace identifiers.
        logger.warning(
            "membership derived from role assignments: %s workspace and %s "
            "organization membership row(s) hold no role assignment and will "
            "not appear as members",
            workspace_uncovered,
            org_uncovered,
        )


def downgrade() -> None:
    # Nothing to undo: this revision changes no schema and no data.
    pass
