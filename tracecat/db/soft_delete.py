"""Global ORM criteria for columns-only soft-deleted rows.

Soft-delete state is encoded as ``deleted_at IS NULL`` for live rows and a
non-NULL timestamp for tombstones. This listener applies only to ORM SELECT
statements that are not column-refresh or relationship-loader queries; pure
Core/text statements, relationship loads, UPDATE, and DELETE statements are
deliberately unfiltered. Relationship loads remain unfiltered so UUID and
historical access paths can still reach tombstones through already-known
objects. Warm ``Session.get()`` identity-map hits also bypass SQL and therefore
bypass this filter.

Use ``with_deleted()`` only for narrow staging, provenance,
sync-reconciliation, and erasure helpers that intentionally need tombstones.
Other opt-outs are a review smell. Active-only product paths should keep their
explicit ``deleted_at IS NULL`` predicates even though this listener makes them
redundant, so rollback remains safe.
"""

from __future__ import annotations

from typing import Final

import sqlalchemy.orm
from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, with_loader_criteria
from sqlalchemy.sql import Executable
from sqlalchemy.sql.elements import ColumnElement

from tracecat.db.models import SoftDeleteMixin

INCLUDE_DELETED: Final[str] = "include_deleted"


def with_deleted[StatementT: Executable](stmt: StatementT) -> StatementT:
    """Opt a statement out of the global soft-delete criteria."""
    return stmt.execution_options(**{INCLUDE_DELETED: True})


def _not_deleted_criteria(cls: type[SoftDeleteMixin]) -> ColumnElement[bool]:
    return cls.deleted_at.is_(None)


@event.listens_for(sqlalchemy.orm.Session, "do_orm_execute")
def _add_soft_delete_criteria(
    execute_state: ORMExecuteState,
) -> None:
    """Apply live-row criteria to top-level ORM SELECT statements."""
    if (
        not execute_state.is_select
        or execute_state.is_column_load
        or execute_state.is_relationship_load
        or execute_state.execution_options.get(INCLUDE_DELETED) is True
    ):
        return

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            SoftDeleteMixin,
            _not_deleted_criteria,
            include_aliases=True,
            propagate_to_loaders=False,
        )
    )


def assert_soft_delete_listener_registered() -> None:
    """Fail fast if the global soft-delete listener was not imported."""
    if event.contains(
        sqlalchemy.orm.Session,
        "do_orm_execute",
        _add_soft_delete_criteria,
    ):
        return
    raise RuntimeError("Global soft-delete ORM listener is not registered")
