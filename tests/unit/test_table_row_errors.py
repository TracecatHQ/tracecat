"""Constraint translation must not hide unexpected database failures."""

import pytest
from asyncpg.exceptions import CheckViolationError, NotNullViolationError
from sqlalchemy.exc import IntegrityError

from tracecat.db.models import TableColumn
from tracecat.tables.service import _execute_row_write


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cause",
    [
        CheckViolationError("unexpected constraint"),
        NotNullViolationError.new({"C": "23502", "c": "__tc_workspace_id"}),
        NotNullViolationError.new({"C": "23502", "c": "id"}),
    ],
)
async def test_unrecognized_constraints_are_not_client_errors(cause: Exception) -> None:
    error = IntegrityError("synthetic SQL", {}, cause)

    async def failed_write() -> None:
        raise error

    with pytest.raises(IntegrityError) as exc:
        await _execute_row_write(failed_write(), [TableColumn(name="record_key")])
    assert exc.value is error
