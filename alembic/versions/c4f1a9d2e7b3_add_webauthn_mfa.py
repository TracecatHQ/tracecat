"""Add WebAuthn security keys, MFA recovery codes, and session MFA state.

Revision ID: c4f1a9d2e7b3
Revises: b99074fdcf3a
Create Date: 2026-10-23 03:30:00.000000

Additive only: new user-level tables and nullable session columns. Existing
sessions keep working until an organization or platform requires MFA.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4f1a9d2e7b3"
down_revision: str | None = "b99074fdcf3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "access_token",
        sa.Column("mfa_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "access_token",
        sa.Column("step_up_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_table(
        "webauthn_credential",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("aaguid", sa.UUID(), nullable=False),
        sa.Column(
            "transports",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "backup_eligible",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "backup_state",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index(
        op.f("ix_webauthn_credential_user_id"),
        "webauthn_credential",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "mfa_recovery_code",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("lookup", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mfa_recovery_code_user_id"),
        "mfa_recovery_code",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mfa_recovery_code_user_id"), table_name="mfa_recovery_code")
    op.drop_table("mfa_recovery_code")
    op.drop_index(
        op.f("ix_webauthn_credential_user_id"), table_name="webauthn_credential"
    )
    op.drop_table("webauthn_credential")
    op.drop_column("access_token", "step_up_verified_at")
    op.drop_column("access_token", "mfa_verified_at")
