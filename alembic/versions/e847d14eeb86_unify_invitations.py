"""unify invitations

Revision ID: e847d14eeb86
Revises: 526f867f6a75
Create Date: 2026-09-10 15:40:00.000000

Makes ``invitation`` the single source of truth for invitations: org-scoped,
with its role grants in the child table ``invitation_grant``. The
``workspace_id`` and ``role_id`` anchor columns are folded into grant rows and
dropped, so foreign keys, not application code, retire a grant whose workspace
or role is deleted.

``organization_invitation`` is deliberately left untouched so the previous app
version keeps working during a rolling deploy. Its live pending rows are copied
verbatim into ``invitation``, keeping their ids and tokens so existing accept
links keep working; expired pending rows are dropped. The contract drop belongs
to a later release.

Duplicate pending rows for one ``(organization, email)`` are merged into the
newest row before a partial unique index makes that invariant a database rule.

"""

from collections.abc import Sequence

import sqlalchemy as sa

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
revision: str = "e847d14eeb86"
down_revision: str | None = "526f867f6a75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# pgcrypto is not installed, so tokens are built from two md5 halves.
_FRESH_TOKEN = "md5(gen_random_uuid()::text) || md5(random()::text)"


def upgrade() -> None:
    op.add_column("invitation", sa.Column("organization_id", sa.UUID(), nullable=True))
    op.add_column(
        "invitation",
        sa.Column(
            "created_by_platform_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE invitation
        SET organization_id = workspace.organization_id
        FROM workspace
        WHERE workspace.id = invitation.workspace_id
          AND invitation.organization_id IS NULL
        """
    )
    op.create_foreign_key(
        op.f("fk_invitation_organization_id_organization"),
        "invitation",
        "organization",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_invitation_organization_id"),
        "invitation",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "invitation_grant",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("invitation_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitation.id"],
            name=op.f("fk_invitation_grant_invitation_id_invitation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_invitation_grant_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_invitation_grant_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_invitation_grant_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitation_grant")),
    )
    op.create_index(
        op.f("ix_invitation_grant_invitation_id"),
        "invitation_grant",
        ["invitation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invitation_grant_organization_id"),
        "invitation_grant",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invitation_grant_role_id"),
        "invitation_grant",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_grant_org_unique",
        "invitation_grant",
        ["invitation_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        "ix_invitation_grant_workspace_unique",
        "invitation_grant",
        ["invitation_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.execute(enable_org_optional_workspace_table_rls("invitation_grant"))

    # Every pre-existing workspace invitation becomes one workspace grant.
    op.execute(
        """
        INSERT INTO invitation_grant (
            id, organization_id, invitation_id, workspace_id, role_id
        )
        SELECT gen_random_uuid(),
               invitation.organization_id,
               invitation.id,
               invitation.workspace_id,
               invitation.role_id
        FROM invitation
        WHERE invitation.workspace_id IS NOT NULL
        """
    )

    # The grant rows now own scope and role, so the anchor columns go.
    op.drop_constraint("uq_invitation_workspace_id_email", "invitation", type_="unique")
    op.drop_constraint(
        "fk_invitation_workspace_id_workspace", "invitation", type_="foreignkey"
    )
    op.drop_constraint("fk_invitation_role_id_role", "invitation", type_="foreignkey")
    op.drop_index(op.f("ix_invitation_workspace_id"), table_name="invitation")
    # The workspace-scoped policy reads workspace_id, so it goes before the column.
    op.execute(disable_workspace_table_rls("invitation"))
    op.drop_column("invitation", "workspace_id")
    op.drop_column("invitation", "role_id")

    # Live pending org rows are copied verbatim, ids and tokens included, so
    # their existing accept links keep working. Expired rows are dead data.
    op.execute(
        "DELETE FROM invitation WHERE status = 'PENDING' AND expires_at <= now()"
    )
    op.execute(
        """
        INSERT INTO invitation (
            id, organization_id, email, status,
            invited_by, token, expires_at, accepted_at,
            created_by_platform_admin, created_at, updated_at
        )
        SELECT oi.id, oi.organization_id, oi.email, oi.status,
               oi.invited_by, oi.token, oi.expires_at, oi.accepted_at,
               oi.created_by_platform_admin, oi.created_at, oi.updated_at
        FROM organization_invitation AS oi
        WHERE oi.status = 'PENDING'
          AND oi.expires_at > now()
          AND NOT EXISTS (SELECT 1 FROM invitation AS i WHERE i.id = oi.id)
        """
    )
    op.execute(
        """
        INSERT INTO invitation_grant (
            id, organization_id, invitation_id, workspace_id, role_id
        )
        SELECT gen_random_uuid(), oi.organization_id, oi.id, NULL, oi.role_id
        FROM organization_invitation AS oi
        JOIN invitation AS i ON i.id = oi.id
        WHERE oi.status = 'PENDING'
          AND NOT EXISTS (
              SELECT 1 FROM invitation_grant AS g
              WHERE g.invitation_id = oi.id AND g.workspace_id IS NULL
          )
        """
    )

    op.alter_column(
        "invitation", "organization_id", existing_type=sa.UUID(), nullable=False
    )

    # Duplicate pending rows merge into the newest; its grant rows adopt the
    # losers' grants, except where that would collide on the per-scope unique.
    op.execute(
        """
        CREATE TEMP TABLE _invitation_merge AS
        WITH ranked AS (
            SELECT id,
                   organization_id,
                   lower(email) AS email_key,
                   row_number() OVER (
                       PARTITION BY organization_id, lower(email)
                       ORDER BY created_at DESC, id DESC
                   ) AS rn
            FROM invitation
            WHERE status = 'PENDING'
        )
        SELECT loser.id AS loser_id, keeper.id AS keeper_id
        FROM ranked AS loser
        JOIN ranked AS keeper
          ON keeper.organization_id = loser.organization_id
         AND keeper.email_key = loser.email_key
         AND keeper.rn = 1
        WHERE loser.rn > 1
        """
    )
    # A loser grant duplicating a scope the keeper already covers is dropped.
    op.execute(
        """
        DELETE FROM invitation_grant AS g
        USING _invitation_merge AS m
        WHERE g.invitation_id = m.loser_id
          AND EXISTS (
              SELECT 1
              FROM invitation_grant AS kept
              WHERE kept.invitation_id = m.keeper_id
                AND kept.workspace_id IS NOT DISTINCT FROM g.workspace_id
          )
        """
    )
    # Two losers may still hold the same scope; keep the first by (created_at, id).
    op.execute(
        """
        DELETE FROM invitation_grant AS g
        USING _invitation_merge AS m
        WHERE g.invitation_id = m.loser_id
          AND EXISTS (
              SELECT 1
              FROM invitation_grant AS other
              JOIN _invitation_merge AS om ON om.loser_id = other.invitation_id
              WHERE om.keeper_id = m.keeper_id
                AND other.workspace_id IS NOT DISTINCT FROM g.workspace_id
                AND (other.created_at, other.id) < (g.created_at, g.id)
          )
        """
    )
    op.execute(
        """
        UPDATE invitation_grant AS g
        SET invitation_id = m.keeper_id
        FROM _invitation_merge AS m
        WHERE g.invitation_id = m.loser_id
        """
    )
    op.execute(
        "DELETE FROM invitation WHERE id IN (SELECT loser_id FROM _invitation_merge)"
    )
    op.execute("DROP TABLE _invitation_merge")

    op.create_index(
        "ix_invitation_org_email_pending_unique",
        "invitation",
        ["organization_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # invitation is now purely org-scoped.
    op.execute(enable_org_table_rls("invitation"))


def downgrade() -> None:
    """Recreate one legacy row per grant.

    Split-off rows get fresh tokens, so the previous app version regenerates
    their links; the first workspace grant of each invitation keeps its token.
    """
    op.execute(disable_org_table_rls("invitation"))
    op.execute(disable_org_optional_workspace_table_rls("invitation_grant"))
    op.drop_index("ix_invitation_org_email_pending_unique", table_name="invitation")
    op.add_column("invitation", sa.Column("workspace_id", sa.UUID(), nullable=True))
    op.add_column("invitation", sa.Column("role_id", sa.UUID(), nullable=True))

    # The first workspace grant is written back into the row itself.
    op.execute(
        """
        UPDATE invitation AS inv
        SET workspace_id = g.workspace_id,
            role_id = g.role_id
        FROM (
            SELECT DISTINCT ON (invitation_id)
                   invitation_id, workspace_id, role_id
            FROM invitation_grant
            WHERE workspace_id IS NOT NULL
            ORDER BY invitation_id, created_at, id
        ) AS g
        WHERE g.invitation_id = inv.id
        """
    )

    # Every further workspace grant becomes its own row, with a fresh token.
    op.execute(
        f"""
        INSERT INTO invitation (
            id, organization_id, email, status, invited_by,
            token, expires_at, accepted_at, created_by_platform_admin,
            workspace_id, role_id, created_at, updated_at
        )
        SELECT gen_random_uuid(), i.organization_id, i.email, i.status,
               i.invited_by, {_FRESH_TOKEN}, i.expires_at, i.accepted_at,
               i.created_by_platform_admin, g.workspace_id, g.role_id,
               i.created_at, i.updated_at
        FROM invitation AS i
        JOIN invitation_grant AS g ON g.invitation_id = i.id
        WHERE g.workspace_id IS NOT NULL
          AND i.workspace_id IS NOT NULL
          AND g.workspace_id <> i.workspace_id
        """
    )

    # invitation is authoritative: a legacy row for the same (email, org) is
    # replaced, so a revocation or acceptance made here is not undone.
    op.execute(
        """
        DELETE FROM organization_invitation AS oi
        USING invitation AS i
        JOIN invitation_grant AS g ON g.invitation_id = i.id
        WHERE g.workspace_id IS NULL
          AND oi.organization_id = i.organization_id
          AND lower(oi.email) = lower(i.email)
        """
    )
    # Org grants go back to the legacy table, keeping the token when the row had
    # no workspace grant to spend it on.
    op.execute(
        f"""
        INSERT INTO organization_invitation (
            id, organization_id, email, role_id, token, status, invited_by,
            expires_at, accepted_at, created_by_platform_admin,
            created_at, updated_at
        )
        SELECT CASE WHEN i.workspace_id IS NULL THEN i.id
                    ELSE gen_random_uuid() END,
               i.organization_id, i.email, g.role_id,
               CASE WHEN i.workspace_id IS NULL THEN i.token
                    ELSE {_FRESH_TOKEN} END,
               i.status, i.invited_by, i.expires_at, i.accepted_at,
               i.created_by_platform_admin, i.created_at, i.updated_at
        FROM invitation AS i
        JOIN invitation_grant AS g ON g.invitation_id = i.id
        WHERE g.workspace_id IS NULL
        """
    )

    # Rows whose only grant was org-scoped now live in the legacy table.
    op.execute("DELETE FROM invitation WHERE workspace_id IS NULL")
    op.execute(
        """
        DELETE FROM invitation
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY workspace_id, email
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM invitation
            ) ranked
            WHERE ranked.rn > 1
        )
        """
    )
    op.alter_column(
        "invitation", "workspace_id", existing_type=sa.UUID(), nullable=False
    )
    op.alter_column("invitation", "role_id", existing_type=sa.UUID(), nullable=False)
    op.create_foreign_key(
        "fk_invitation_workspace_id_workspace",
        "invitation",
        "workspace",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invitation_role_id_role",
        "invitation",
        "role",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_invitation_workspace_id"), "invitation", ["workspace_id"], unique=False
    )
    op.create_unique_constraint(
        "uq_invitation_workspace_id_email", "invitation", ["workspace_id", "email"]
    )

    op.drop_index("ix_invitation_grant_workspace_unique", table_name="invitation_grant")
    op.drop_index("ix_invitation_grant_org_unique", table_name="invitation_grant")
    op.drop_index(op.f("ix_invitation_grant_role_id"), table_name="invitation_grant")
    op.drop_index(
        op.f("ix_invitation_grant_organization_id"), table_name="invitation_grant"
    )
    op.drop_index(
        op.f("ix_invitation_grant_invitation_id"), table_name="invitation_grant"
    )
    op.drop_table("invitation_grant")

    op.drop_index(op.f("ix_invitation_organization_id"), table_name="invitation")
    op.drop_constraint(
        op.f("fk_invitation_organization_id_organization"),
        "invitation",
        type_="foreignkey",
    )
    op.drop_column("invitation", "organization_id")
    op.drop_column("invitation", "created_by_platform_admin")

    op.execute(enable_workspace_table_rls("invitation"))
