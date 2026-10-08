"""Row validation and constraint translation preserve safe error boundaries."""

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from asyncpg.exceptions import CheckViolationError, NotNullViolationError
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Table, TableColumn
from tracecat.tables.enums import SqlType
from tracecat.tables.exceptions import TableRowError
from tracecat.tables.service import BaseTablesService, _execute_row_write


@pytest.fixture
def tables_service() -> BaseTablesService:
    return BaseTablesService(
        session=AsyncMock(spec=AsyncSession),
        role=Role(
            type="service",
            service_id="tracecat-api",
            organization_id=uuid4(),
            workspace_id=uuid4(),
        ),
    )


@pytest.fixture
def json_table() -> Table:
    return Table(
        name="test_table",
        columns=[TableColumn(name="payload", type=SqlType.JSONB, nullable=True)],
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("nested", [False, True])
def test_json_row_values_reject_non_finite_numbers(
    tables_service: BaseTablesService, json_table: Table, value: float, nested: bool
) -> None:
    payload = {"items": [value]} if nested else value

    with pytest.raises(TableRowError) as exc:
        tables_service._normalize_row_inputs(json_table, {"payload": payload})

    assert exc.value.code == "invalid_value"
    assert exc.value.detail == {
        "code": "invalid_value",
        "column": "payload",
        "message": "Column 'payload' requires a valid JSONB value.",
    }
    assert exc.value.__context__ is None


@pytest.mark.parametrize("value", [1.5, "NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("nested", [False, True])
def test_json_row_values_preserve_finite_numbers_and_strings(
    tables_service: BaseTablesService,
    json_table: Table,
    value: float | str,
    nested: bool,
) -> None:
    payload = {"items": [value]} if nested else value

    assert tables_service._normalize_row_inputs(json_table, {"payload": payload}) == {
        "payload": payload
    }


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [DBAPIError, IntegrityError])
@pytest.mark.parametrize(
    "cause",
    [
        RuntimeError("unexpected database failure"),
        CheckViolationError("unexpected constraint"),
        NotNullViolationError.new({"C": "23502", "c": "__tc_workspace_id"}),
        NotNullViolationError.new({"C": "23502", "c": "id"}),
    ],
)
async def test_unrecognized_database_errors_are_not_client_errors(
    cause: Exception, error_type: type[DBAPIError]
) -> None:
    error = error_type("synthetic SQL", {}, cause)

    async def failed_write() -> None:
        raise error

    with pytest.raises(error_type) as exc:
        await _execute_row_write(failed_write(), [TableColumn(name="record_key")])
    assert exc.value is error


@pytest.mark.anyio
async def test_update_row_rejects_empty_data(
    tables_service: BaseTablesService, json_table: Table
) -> None:
    with pytest.raises(TableRowError) as exc:
        await tables_service.update_row(json_table, uuid4(), {})

    assert exc.value.detail == {
        "code": "empty_update",
        "column": None,
        "message": "Row update must include at least one column.",
    }
    cast(AsyncMock, tables_service.session).connection.assert_not_called()
