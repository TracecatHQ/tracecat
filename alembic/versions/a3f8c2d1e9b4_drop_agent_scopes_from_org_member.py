"""Drop agent scopes from the organization-member role.

The member floor no longer carries agent:read or agent:execute; agent access
comes only from workspace roles. Earlier role seeding was insert-only, so
existing organization-member roles still link to both scopes, and legacy
assignments of that role would keep granting them. This removes those links
before the first startup; seeding now also removes links a preset role's
definition does not carry.

Revision ID: a3f8c2d1e9b4
Revises: 3b8f2c1d9e7a
Create Date: 2026-10-14 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3f8c2d1e9b4"
down_revision: str | None = "3b8f2c1d9e7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM role_scope
        USING role, scope
        WHERE role_scope.role_id = role.id
          AND role_scope.scope_id = scope.id
          AND role.slug = 'organization-member'
          AND scope.organization_id IS NULL
          AND scope.name IN ('agent:read', 'agent:execute')
        """
    )


def downgrade() -> None:
    # Restores the links the previous app version seeds for this role.
    op.execute(
        """
        INSERT INTO role_scope (role_id, scope_id)
        SELECT role.id, scope.id
        FROM role
        CROSS JOIN scope
        WHERE role.slug = 'organization-member'
          AND scope.organization_id IS NULL
          AND scope.name IN ('agent:read', 'agent:execute')
        ON CONFLICT (role_id, scope_id) DO NOTHING
        """
    )
