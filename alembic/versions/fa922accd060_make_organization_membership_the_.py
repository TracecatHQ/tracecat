"""make organization membership the aggregate root

Revision ID: fa922accd060
Revises: a7c3e9f1b2d4
Create Date: 2026-09-16 00:00:00.000000

Makes ``organization_membership`` the parent of everything a user holds in an
organization. Expand only: no table is dropped and the previous app version
keeps working (it writes the membership row before any child and deletes
children before the parent, so the new cascades are no-ops for it).

Steps, in order:

1. ``group_member.organization_id`` is added nullable and filled from ``group``.
   It stays nullable this release because old pods insert without it; NULL in a
   composite foreign key is unchecked, so those rows are admitted.
2. Missing membership rows are backfilled from existing ``user_role_assignment``
   and ``group_member`` rows, so every existing grant has a parent.
3. ``service_account.owner_user_id`` is set to NULL where the owner holds no
   membership row. Removal never cleared this pointer before, and ownership
   metadata must not become membership.
4. Composite foreign keys ``(organization_id, user_id)`` are added from
   ``user_role_assignment`` and ``group_member`` with ``ON DELETE CASCADE``,
   each as ``NOT VALID`` then ``VALIDATE`` so the table is never scanned under
   an exclusive lock.
5. The owner foreign key on ``service_account`` is moved from ``user`` to the
   membership row with ``ON DELETE SET NULL (owner_user_id)``, so removing the
   owner nulls only that column and the organization keeps the account.
6. A row trigger on ``organization_membership`` revokes the user's MCP personal
   access tokens and refresh tokens for that organization on delete. They are
   revoked, not cascaded, so the rows keep ``user_id`` for attribution, and
   ``revoked_by`` is taken from the ``app.current_user_id`` setting when the
   caller set one.

Downgrade reverses 6, 5, 4 and 1. The backfilled membership rows and nulled
owners are left in place; both are valid under the previous schema. The nulled
owner IDs are not recoverable: they pointed at users already removed from the
organization and carried no runtime meaning, so the loss is accepted rather
than blocking downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_table_rls,
    enable_group_member_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "fa922accd060"
down_revision: str | None = "bc3124ad3437"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(enable_group_member_table_rls())
    op.add_column(
        "group_member",
        sa.Column("organization_id", sa.UUID(), nullable=True),
    )
    op.execute(
        """
        UPDATE group_member AS gm
        SET organization_id = g.organization_id
        FROM "group" AS g
        WHERE g.id = gm.group_id
          AND gm.organization_id IS NULL
        """
    )

    # Children below are FK'd to the membership row, so every org/user pair they
    # reference must exist first.
    op.execute(
        """
        INSERT INTO organization_membership (user_id, organization_id)
        SELECT DISTINCT user_id, organization_id
        FROM user_role_assignment
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO organization_membership (user_id, organization_id)
        SELECT DISTINCT gm.user_id, gm.organization_id
        FROM group_member AS gm
        WHERE gm.organization_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    # Ownership metadata must not become membership: an owner who was already
    # removed from the organization is cleared instead of being re-admitted.
    op.execute(
        """
        UPDATE service_account
        SET owner_user_id = NULL
        WHERE owner_user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM organization_membership m
              WHERE m.organization_id = service_account.organization_id
                AND m.user_id = service_account.owner_user_id
          )
        """
    )

    # NOT VALID keeps the ALTER from scanning the table under an exclusive lock;
    # the separate VALIDATE takes only a SHARE UPDATE EXCLUSIVE lock.
    op.execute(
        """
        ALTER TABLE user_role_assignment
        ADD CONSTRAINT fk_user_role_assignment_org_membership
        FOREIGN KEY (organization_id, user_id)
        REFERENCES organization_membership (organization_id, user_id)
        ON DELETE CASCADE
        NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE user_role_assignment "
        "VALIDATE CONSTRAINT fk_user_role_assignment_org_membership"
    )

    op.execute(
        """
        ALTER TABLE group_member
        ADD CONSTRAINT fk_group_member_org_membership
        FOREIGN KEY (organization_id, user_id)
        REFERENCES organization_membership (organization_id, user_id)
        ON DELETE CASCADE
        NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE group_member VALIDATE CONSTRAINT fk_group_member_org_membership"
    )

    # Column-list SET NULL so removing the owner never nulls organization_id.
    op.drop_constraint(
        "fk_service_account_owner_user_id_user", "service_account", type_="foreignkey"
    )
    op.execute(
        """
        ALTER TABLE service_account
        ADD CONSTRAINT fk_service_account_owner_org_membership
        FOREIGN KEY (organization_id, owner_user_id)
        REFERENCES organization_membership (organization_id, user_id)
        ON DELETE SET NULL (owner_user_id)
        NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE service_account "
        "VALIDATE CONSTRAINT fk_service_account_owner_org_membership"
    )

    # MCP tokens keep user_id for attribution, so they are revoked rather than
    # cascaded when a member is removed.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION revoke_mcp_tokens_on_membership_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE mcp_personal_access_token
            SET revoked_at = now(),
                revoked_by = NULLIF(
                    current_setting('app.current_user_id', true), ''
                )::uuid
            WHERE user_id = OLD.user_id
              AND organization_id = OLD.organization_id
              AND revoked_at IS NULL;

            UPDATE mcp_refresh_token
            SET status = 'revoked'
            WHERE user_id = OLD.user_id
              AND organization_id = OLD.organization_id
              AND status = 'active';

            RETURN OLD;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_membership_revoke_mcp_tokens
        AFTER DELETE ON organization_membership
        FOR EACH ROW
        EXECUTE FUNCTION revoke_mcp_tokens_on_membership_delete()
        """
    )


def downgrade() -> None:
    op.execute(disable_org_table_rls("group_member"))
    op.execute(
        "DROP TRIGGER IF EXISTS trg_organization_membership_revoke_mcp_tokens "
        "ON organization_membership"
    )
    op.execute("DROP FUNCTION IF EXISTS revoke_mcp_tokens_on_membership_delete()")

    op.drop_constraint(
        "fk_service_account_owner_org_membership",
        "service_account",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_service_account_owner_user_id_user",
        "service_account",
        "user",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "fk_group_member_org_membership", "group_member", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_user_role_assignment_org_membership",
        "user_role_assignment",
        type_="foreignkey",
    )

    op.drop_column("group_member", "organization_id")
