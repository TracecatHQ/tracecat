"""Index the workflow and case foreign keys that eager loads filter on.

Revision ID: 8c04c3aa6e35
Revises: 21042b4645f1
Create Date: 2026-10-29 12:20:36.041015

Every ``select(Workflow)`` eager-loads its actions, webhook and schedules with
``WHERE <table>.workflow_id IN (...)``, and the case list counts and sorts by
``case_task`` rows per case. None of these foreign keys had an index, so each
load was a sequential scan of the whole table across all workspaces. The same
columns drive the ON DELETE CASCADE lookups when a workflow or case is deleted.

The indexes lead with the foreign key, not ``workspace_id``: the eager loads and
cascade lookups filter on the foreign key alone, and PostgreSQL 16 cannot skip
a leading column. Workspace-scoped queries still use them because the foreign
key is far more selective than the workspace.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c04c3aa6e35"
down_revision: str | None = "21042b4645f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_action_workflow_id", "action", "workflow_id"),
    ("ix_webhook_workflow_id", "webhook", "workflow_id"),
    ("ix_schedule_workflow_id", "schedule", "workflow_id"),
    ("ix_workflow_tag_link_workflow_id", "workflow_tag_link", "workflow_id"),
    ("ix_case_task_case_id", "case_task", "case_id"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, column in _INDEXES:
            # A failed concurrent build leaves an invalid index behind. Rebuild
            # it so a retry never adopts a partial index.
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
            op.execute(f'CREATE INDEX CONCURRENTLY "{name}" ON "{table}" ({column})')


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _, _ in reversed(_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
