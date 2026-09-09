"""Prepare transactional membership writes for the assignment cutover.

Revision ID: 4134d4ebdc69
Revises: 526f867f6a75
Create Date: 2026-09-08 17:43:26.780886

Release 1 retains the membership admission gate and writes membership alongside
explicit assignment grants and revocations. This migration changes no roles,
scopes, assignments, or membership rows. Old writers remain supported.

After old writers drain, a later release can convert remaining membership state
and switch readers. That conversion must preserve effective access in both
directions: missing assignments must not become default permission grants, and
assignments without membership must not become new access. Keep compatibility
writes until supported rollback versions no longer need the old tables.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4134d4ebdc69"
down_revision: str | None = "526f867f6a75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    # Only the additional writer policy changes; authorization data is untouched.
    op.execute("DROP POLICY rls_policy_membership_org ON membership")
