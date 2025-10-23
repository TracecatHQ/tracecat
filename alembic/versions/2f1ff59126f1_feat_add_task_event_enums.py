"""feat: add task event enums

Revision ID: 2f1ff59126f1
Revises: 799af90b73bf
Create Date: 2025-10-23 12:37:42.555096

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f1ff59126f1"
down_revision: str | None = "799af90b73bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'caseeventtype' AND e.enumlabel = 'TASK_CREATED'
      ) THEN
        EXECUTE 'ALTER TYPE caseeventtype ADD VALUE ''TASK_CREATED''';
      END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'caseeventtype' AND e.enumlabel = 'TASK_DELETED'
      ) THEN
        EXECUTE 'ALTER TYPE caseeventtype ADD VALUE ''TASK_DELETED''';
      END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'caseeventtype' AND e.enumlabel = 'TASK_STATUS_CHANGED'
      ) THEN
        EXECUTE 'ALTER TYPE caseeventtype ADD VALUE ''TASK_STATUS_CHANGED''';
      END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'caseeventtype' AND e.enumlabel = 'TASK_PRIORITY_CHANGED'
      ) THEN
        EXECUTE 'ALTER TYPE caseeventtype ADD VALUE ''TASK_PRIORITY_CHANGED''';
      END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'caseeventtype' AND e.enumlabel = 'TASK_WORKFLOW_CHANGED'
      ) THEN
        EXECUTE 'ALTER TYPE caseeventtype ADD VALUE ''TASK_WORKFLOW_CHANGED''';
      END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'caseeventtype' AND e.enumlabel = 'TASK_ASSIGNEE_CHANGED'
      ) THEN
        EXECUTE 'ALTER TYPE caseeventtype ADD VALUE ''TASK_ASSIGNEE_CHANGED''';
      END IF;
    END$$;
    """)


def downgrade() -> None:
    # Postgres doesn't support removing enum values; leave empty
    pass
