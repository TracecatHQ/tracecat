"""make organization membership the aggregate root

Revision ID: fa922accd060
Revises: a7c3e9f1b2d4
Create Date: 2026-09-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa922accd060"
down_revision: str | None = "a7c3e9f1b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A member stays present while they hold any direct assignment or any group link
# in the organization; losing the last one removes them.
DROP_MEMBERSHIP_FUNCTION = """
CREATE OR REPLACE FUNCTION drop_membership_without_paths()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    org uuid;
BEGIN
    -- OLD has no group_id on user_role_assignment, so resolve per table rather
    -- than coalescing across both shapes.
    IF TG_TABLE_NAME = 'group_member' THEN
        org := COALESCE(
            OLD.organization_id,
            (SELECT organization_id FROM "group" WHERE id = OLD.group_id)
        );
    ELSE
        org := OLD.organization_id;
    END IF;
    IF org IS NULL THEN
        RETURN NULL;
    END IF;

    -- Superusers are never deletable, so a pathless one keeps the membership
    -- row rather than being evicted.
    IF EXISTS (
        SELECT 1 FROM "user" WHERE id = OLD.user_id AND is_superuser
    ) THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM user_role_assignment
        WHERE organization_id = org AND user_id = OLD.user_id
    ) AND NOT EXISTS (
        SELECT 1 FROM group_member gm
        JOIN "group" g ON g.id = gm.group_id
        WHERE gm.user_id = OLD.user_id AND g.organization_id = org
    ) THEN
        DELETE FROM organization_membership
        WHERE organization_id = org AND user_id = OLD.user_id;
    END IF;

    RETURN NULL;
END;
$$;
"""

# Deferred so a replace-assignments transaction that deletes then reinserts
# never evicts mid-transaction.
DROP_MEMBERSHIP_TRIGGERS = (
    """
    CREATE CONSTRAINT TRIGGER trg_user_role_assignment_drop_membership
    AFTER DELETE ON user_role_assignment
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION drop_membership_without_paths()
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_group_member_drop_membership
    AFTER DELETE ON group_member
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION drop_membership_without_paths()
    """,
)


def upgrade() -> None:
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
    op.execute(
        """
        INSERT INTO organization_membership (user_id, organization_id)
        SELECT DISTINCT sa.owner_user_id, sa.organization_id
        FROM service_account AS sa
        WHERE sa.owner_user_id IS NOT NULL
        ON CONFLICT DO NOTHING
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
            SET revoked_at = now()
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

    op.execute(DROP_MEMBERSHIP_FUNCTION)
    for statement in DROP_MEMBERSHIP_TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_role_assignment_drop_membership "
        "ON user_role_assignment"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_group_member_drop_membership ON group_member"
    )
    op.execute("DROP FUNCTION IF EXISTS drop_membership_without_paths()")

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
