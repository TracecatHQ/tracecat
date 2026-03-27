"""unify secret type casing to snake_case

Revision ID: 3431033d29fd
Revises: 0a1e3100a432
Create Date: 2026-03-27 13:29:19.532489

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3431033d29fd"
down_revision: str | None = "0a1e3100a432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (old_value, new_value)
_RENAMES = [
    ("ssh-key", "ssh_key"),
    ("ca-cert", "ca_cert"),
    ("github-app", "github_app"),
]

_TABLES = ["secret", "organization_secret", "platform_secret"]


def upgrade() -> None:
    """Rename kebab-case secret type values to snake_case."""
    for table in _TABLES:
        for old, new in _RENAMES:
            op.execute(
                f"UPDATE {table} SET type = '{new}' WHERE type = '{old}'"  # noqa: S608
            )


def downgrade() -> None:
    """Revert snake_case secret type values to kebab-case."""
    for table in _TABLES:
        for old, new in _RENAMES:
            op.execute(
                f"UPDATE {table} SET type = '{old}' WHERE type = '{new}'"  # noqa: S608
            )
