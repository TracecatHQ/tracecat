"""clamp agent action timeouts to default floor

Revision ID: f1626d4aa9d8
Revises: c20c04c7d2a9
Create Date: 2026-08-20 15:22:25.322789

Agent-backed actions (``ai.agent``, ``ai.action``, ``ai.preset_agent``) created
before configurable timeouts carry the generic 300s default baked into
``control_flow.retry_policy.timeout``. Execution already clamps these up to the
agent default at parse time; this backfill makes the stored value match so the
builder displays the truth and the write-time bounds guard does not reject
untouched legacy nodes on their next save.

Values at or above the default are left as-is (the runtime clamps to the
deployment ceiling on read). Missing or malformed timeouts normalize to the
default; values of 10+ digits (beyond any plausible timeout, and beyond
PostgreSQL's int4 cast) are treated as malformed rather than risking an
integer-out-of-range abort in the pre-upgrade job.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1626d4aa9d8"
down_revision: str | None = "c20c04c7d2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors AGENT_TIMEOUT_SECONDS_DEFAULT in tracecat/agent/constants.py at the
# time of this revision. Intentionally a literal: migrations must not drift
# with future constant changes.
AGENT_TIMEOUT_SECONDS_DEFAULT = 1800


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE action
            SET control_flow = jsonb_set(
                coalesce(control_flow, '{}'::jsonb),
                '{retry_policy}',
                coalesce(control_flow -> 'retry_policy', '{}'::jsonb)
                    || jsonb_build_object('timeout', :default_timeout)
            )
            WHERE type IN ('ai.agent', 'ai.action', 'ai.preset_agent')
              AND coalesce(
                    CASE
                        WHEN control_flow -> 'retry_policy' ->> 'timeout'
                            ~ '^[0-9]{1,9}$'
                        THEN (control_flow -> 'retry_policy' ->> 'timeout')::int
                    END,
                    0
                  ) < :default_timeout
            """
        ).bindparams(default_timeout=AGENT_TIMEOUT_SECONDS_DEFAULT)
    )


def downgrade() -> None:
    raise NotImplementedError(
        "This data backfill is one-way: the original out-of-bounds timeout "
        "values are not recoverable. The clamped values are valid for every "
        "prior app version (whose runtime clamps identically at parse time), "
        "so rolling back the application does not require reverting this "
        "revision. If the pre-migration values themselves must be restored, "
        "restore the database from a backup or snapshot taken before this "
        "revision, then `alembic stamp c20c04c7d2a9`."
    )
