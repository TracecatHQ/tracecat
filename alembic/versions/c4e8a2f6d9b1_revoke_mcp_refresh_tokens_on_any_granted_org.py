"""Revoke MCP refresh tokens when any granted org membership is removed.

Revision ID: c4e8a2f6d9b1
Revises: b7d41c9e2a6f
Create Date: 2026-10-23 21:30:00.000000

Adds a nullable ``mcp_refresh_token.granted_organization_ids`` column and
extends the membership-delete trigger to match it. Rows with NULL keep the
previous behavior (match on ``organization_id`` only).

The trigger becomes SECURITY DEFINER: the deleting session is scoped to the
org being left, so tenant RLS would otherwise hide tokens whose primary org is
another one. It also takes the per-user advisory lock that refresh-token
rotation holds, so a rotation that read the memberships before the delete
commits its replacement token before the trigger looks for it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4e8a2f6d9b1"
down_revision: str | None = "b7d41c9e2a6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_refresh_token",
        sa.Column(
            "granted_organization_ids",
            postgresql.ARRAY(sa.UUID()),
            nullable=True,
        ),
    )
    # The lock key must match lock_mcp_refresh_token_users in
    # tracecat/db/locks.py.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.revoke_mcp_tokens_on_membership_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('mcp_refresh_token:' || OLD.user_id::text, 0)
            );

            UPDATE public.mcp_personal_access_token
            SET revoked_at = now(),
                revoked_by = NULLIF(
                    current_setting('app.current_user_id', true), ''
                )::uuid
            WHERE user_id = OLD.user_id
              AND organization_id = OLD.organization_id
              AND revoked_at IS NULL;

            UPDATE public.mcp_refresh_token
            SET status = 'revoked'
            WHERE user_id = OLD.user_id
              AND (
                  organization_id = OLD.organization_id
                  OR OLD.organization_id = ANY(granted_organization_ids)
              )
              AND status = 'active';

            RETURN OLD;
        END;
        $$;
        """
    )


def downgrade() -> None:
    # CREATE OR REPLACE resets SECURITY DEFINER and the pinned search_path.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.revoke_mcp_tokens_on_membership_delete()
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
    op.drop_column("mcp_refresh_token", "granted_organization_ids")
