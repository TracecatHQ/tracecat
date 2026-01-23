"""Tests for core.table UDFs in the registry."""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import tracecat_registry.core.table as table_core
from asyncpg import DuplicateTableError
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from tracecat_registry.core.table import (
    create_table,
    delete_row,
    download,
    get_table_metadata,
    insert_row,
    insert_rows,
    is_in,
    list_tables,
    lookup,
    lookup_many,
    search_rows,
    update_row,
)

from tracecat import config
from tracecat.auth.types import AccessLevel, Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Workspace
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.tables.enums import SqlType
from tracecat.tables.service import TablesService


class _FakeTablesClient:
    async def _call_direct(self, func, *args, **kwargs):
        with patch("tracecat_registry.config.flags.registry_client", False):
            return await func(*args, **kwargs)

    async def lookup(self, *, table: str, column: str, value: object):
        return await self._call_direct(lookup, table=table, column=column, value=value)

    async def exists(self, *, table: str, column: str, value: object) -> bool:
        return await self._call_direct(is_in, table=table, column=column, value=value)

    async def lookup_many(
        self,
        *,
        table: str,
        column: str,
        value: object,
        limit: int | None = None,
    ):
        return await self._call_direct(
            lookup_many, table=table, column=column, value=value, limit=limit
        )

    async def search_rows(
        self,
        *,
        table: str,
        search_term: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
        offset: int = 0,
        limit: int = 100,
    ):
        return await self._call_direct(
            search_rows,
            table=table,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
            offset=offset,
            limit=limit,
        )

    async def insert_row(
        self, *, table: str, row_data: dict[str, object], upsert: bool = False
    ):
        return await self._call_direct(
            insert_row, table=table, row_data=row_data, upsert=upsert
        )

    async def insert_rows(
        self,
        *,
        table: str,
        rows_data: list[dict[str, object]],
        upsert: bool = False,
    ) -> int:
        return await self._call_direct(
            insert_rows, table=table, rows_data=rows_data, upsert=upsert
        )

    async def update_row(
        self,
        *,
        table: str,
        row_id: str,
        row_data: dict[str, object],
    ):
        return await self._call_direct(
            update_row, table=table, row_id=row_id, row_data=row_data
        )

    async def delete_row(self, *, table: str, row_id: str) -> None:
        await self._call_direct(delete_row, table=table, row_id=row_id)

    async def create_table(
        self,
        *,
        name: str,
        columns: list[dict[str, object]] | None = None,
        raise_on_duplicate: bool = True,
    ):
        return await self._call_direct(
            create_table,
            name=name,
            columns=columns,
            raise_on_duplicate=raise_on_duplicate,
        )

    async def list_tables(self):
        return await self._call_direct(list_tables)

    async def get_table_metadata(self, name: str):
        return await self._call_direct(get_table_metadata, name=name)

    async def download(
        self,
        *,
        table: str,
        format: str | None = None,
        limit: int = 1000,
    ):
        return await self._call_direct(download, name=table, format=format, limit=limit)


@pytest.fixture(params=[False, True], ids=["registry_client_off", "registry_client_on"])
def registry_client_enabled(request) -> bool:
    """Toggle the REGISTRY_CLIENT feature flag."""
    return request.param


@pytest.fixture(autouse=True)
def registry_client_ctx(monkeypatch: pytest.MonkeyPatch, registry_client_enabled: bool):
    monkeypatch.setattr(
        table_core.config.flags, "registry_client", registry_client_enabled
    )
    if registry_client_enabled:
        fake_ctx = SimpleNamespace(tables=_FakeTablesClient())
        monkeypatch.setattr(table_core, "get_context", lambda: fake_ctx)
    yield


@pytest.fixture
def mock_table():
    """Create a mock table for testing."""
    table = MagicMock()
    table.id = uuid.uuid4()
    table.name = "test_table"
    table.to_dict.return_value = {
        "id": str(table.id),
        "name": table.name,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    return table


@pytest.fixture
def mock_row():
    """Create a mock row for testing."""
    return {
        "id": str(uuid.uuid4()),
        "name": "John Doe",
        "age": 30,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


@pytest.mark.anyio
class TestCoreLookup:
    """Test cases for the lookup UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_lookup_success(self, mock_with_session, mock_row):
        """Test successful single row lookup."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.lookup_rows.return_value = [mock_row]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the lookup function
        result = await lookup(
            table="test_table",
            column="name",
            value="John Doe",
        )

        # Assert lookup_rows was called with expected parameters
        mock_service.lookup_rows.assert_called_once()
        call_kwargs = mock_service.lookup_rows.call_args[1]
        assert call_kwargs["table_name"] == "test_table"
        assert call_kwargs["columns"] == ["name"]
        assert call_kwargs["values"] == ["John Doe"]
        assert call_kwargs["limit"] == 1

        # Verify the result
        assert result == mock_row

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_lookup_not_found(self, mock_with_session):
        """Test lookup when no row is found."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.lookup_rows.return_value = []

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the lookup function
        result = await lookup(
            table="test_table",
            column="name",
            value="Nonexistent",
        )

        # Verify the result is None
        assert result is None


@pytest.mark.anyio
class TestCoreIsInTable:
    """Test cases for the is_in_table UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_is_in_table_true(self, mock_with_session, mock_row):
        """Returns True when at least one matching row exists."""
        mock_service = AsyncMock()
        mock_service.exists_rows.return_value = True

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await is_in(
            table="test_table",
            column="name",
            value="John Doe",
        )

        mock_service.exists_rows.assert_called_once()
        call_kwargs = mock_service.exists_rows.call_args[1]
        assert call_kwargs["table_name"] == "test_table"
        assert call_kwargs["columns"] == ["name"]
        assert call_kwargs["values"] == ["John Doe"]
        assert result is True

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_is_in_table_false(self, mock_with_session):
        """Returns False when no matching row exists."""
        mock_service = AsyncMock()
        mock_service.exists_rows.return_value = False

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await is_in(
            table="test_table",
            column="name",
            value="Nonexistent",
        )

        assert result is False


@pytest.mark.anyio
class TestCoreLookupMany:
    """Test cases for the lookup_many UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_lookup_many_success(self, mock_with_session, mock_row):
        """Test successful multiple row lookup."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_rows = [mock_row, {**mock_row, "id": str(uuid.uuid4()), "age": 25}]
        mock_service.lookup_rows.return_value = mock_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the lookup_many function
        result = await lookup_many(
            table="test_table",
            column="name",
            value="John Doe",
            limit=50,
        )

        # Assert lookup_rows was called with expected parameters
        mock_service.lookup_rows.assert_called_once()
        call_kwargs = mock_service.lookup_rows.call_args[1]
        assert call_kwargs["table_name"] == "test_table"
        assert call_kwargs["columns"] == ["name"]
        assert call_kwargs["values"] == ["John Doe"]
        assert call_kwargs["limit"] == 50

        # Verify the result
        assert result == mock_rows

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_lookup_many_limit_validation(self, mock_with_session):
        """Test that lookup_many raises ValueError when limit exceeds maximum."""
        from tracecat.config import TRACECAT__MAX_ROWS_CLIENT_POSTGRES

        # Call lookup_many with limit exceeding maximum
        with pytest.raises(
            ValueError,
            match=f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}",
        ):
            await lookup_many(
                table="test_table",
                column="name",
                value="John Doe",
                limit=TRACECAT__MAX_ROWS_CLIENT_POSTGRES + 1,
            )

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_lookup_many_with_date_filters(self, mock_with_session, mock_row):
        """Test lookup_many with date filtering capabilities."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.lookup_rows.return_value = [mock_row]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Note: lookup_many doesn't support date filters, so this test is removed
        # Call the lookup_many function with just basic parameters
        result = await lookup_many(
            table="test_table",
            column="name",
            value="John Doe",
            limit=50,
        )

        # Assert lookup_rows was called with expected parameters (no date filters)
        mock_service.lookup_rows.assert_called_once()
        call_kwargs = mock_service.lookup_rows.call_args[1]
        assert call_kwargs["table_name"] == "test_table"
        assert call_kwargs["columns"] == ["name"]
        assert call_kwargs["values"] == ["John Doe"]
        assert call_kwargs["limit"] == 50

        # Verify the result
        assert result == [mock_row]


@pytest.mark.anyio
class TestCoreInsertRow:
    """Test cases for the insert_row UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_insert_row_success(self, mock_with_session, mock_table, mock_row):
        """Test successful row insertion."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.insert_row.return_value = mock_row

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the insert_row function
        result = await insert_row(
            table="test_table",
            row_data={"name": "John Doe", "age": 30},
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert insert_row was called with expected parameters
        mock_service.insert_row.assert_called_once()
        call_kwargs = mock_service.insert_row.call_args[1]
        assert call_kwargs["table"] is mock_table
        assert call_kwargs["params"].data == {"name": "John Doe", "age": 30}
        assert call_kwargs["params"].upsert is False

        # Verify the result
        assert result == mock_row

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_insert_row_with_upsert(
        self, mock_with_session, mock_table, mock_row
    ):
        """Test row insertion with upsert enabled."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.insert_row.return_value = mock_row

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the insert_row function with upsert
        result = await insert_row(
            table="test_table",
            row_data={"name": "John Doe", "age": 30},
            upsert=True,
        )

        # Assert insert_row was called with upsert=True
        mock_service.insert_row.assert_called_once()
        call_kwargs = mock_service.insert_row.call_args[1]
        assert call_kwargs["table"] is mock_table
        assert call_kwargs["params"].data == {"name": "John Doe", "age": 30}
        assert call_kwargs["params"].upsert is True

        # Verify the result
        assert result == mock_row


@pytest.mark.anyio
class TestCoreUpdateRow:
    """Test cases for the update_row UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_update_row_success(self, mock_with_session, mock_table, mock_row):
        """Test successful row update."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        updated_row = {**mock_row, "age": 31}
        mock_service.update_row.return_value = updated_row

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update_row function
        result = await update_row(
            table="test_table",
            row_id=mock_row["id"],
            row_data={"age": 31},
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert update_row was called with expected parameters
        mock_service.update_row.assert_called_once_with(
            table=mock_table, row_id=uuid.UUID(mock_row["id"]), data={"age": 31}
        )

        # Verify the result
        assert result == updated_row


@pytest.mark.anyio
class TestCoreDeleteRow:
    """Test cases for the delete_row UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_delete_row_success(self, mock_with_session, mock_table, mock_row):
        """Test successful row deletion."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.delete_row.return_value = None

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the delete_row function
        await delete_row(
            table="test_table",
            row_id=mock_row["id"],
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert delete_row was called with expected parameters
        mock_service.delete_row.assert_called_once_with(
            table=mock_table, row_id=uuid.UUID(mock_row["id"])
        )


@pytest.mark.anyio
class TestCoreCreateTable:
    """Test cases for the create_table UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_create_table_without_columns(self, mock_with_session, mock_table):
        """Test table creation without predefined columns."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.create_table.return_value = mock_table

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the create_table function
        result = await create_table(name="test_table")

        # Assert create_table was called with expected parameters
        mock_service.create_table.assert_called_once()
        table_create_arg = mock_service.create_table.call_args[0][0]
        assert table_create_arg.name == "test_table"
        assert table_create_arg.columns == []

        # Verify the result matches the table's dict representation
        assert result == mock_table.to_dict.return_value

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_create_table_with_columns(self, mock_with_session, mock_table):
        """Test table creation with predefined columns."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.create_table.return_value = mock_table

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Define columns
        columns = [
            {"name": "name", "type": "TEXT", "nullable": True, "default": None},
            {"name": "age", "type": "INTEGER", "nullable": False, "default": 0},
        ]

        # Call the create_table function
        result = await create_table(name="test_table", columns=columns)

        # Assert create_table was called with expected parameters
        mock_service.create_table.assert_called_once()
        table_create_arg = mock_service.create_table.call_args[0][0]
        assert table_create_arg.name == "test_table"
        assert len(table_create_arg.columns) == 2

        # Check first column
        col1 = table_create_arg.columns[0]
        assert col1.name == "name"
        assert col1.type == SqlType.TEXT
        assert col1.nullable is True
        assert col1.default is None

        # Check second column
        col2 = table_create_arg.columns[1]
        assert col2.name == "age"
        assert col2.type == SqlType.INTEGER
        assert col2.nullable is False
        assert col2.default == 0

        # Verify the result matches the table's dict representation
        assert result == mock_table.to_dict.return_value

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_create_table_raise_on_duplicate_true_raises_on_duplicate(
        self, mock_with_session, mock_table
    ):
        """Test that create_table raises error when table exists and raise_on_duplicate=True."""
        # Set up the mock service context manager
        mock_service = AsyncMock()

        # Create a chain of exceptions: ProgrammingError wrapping DuplicateTableError
        # SQLAlchemy's ProgrammingError chains exceptions via __cause__
        duplicate_error = DuplicateTableError("relation already exists")
        programming_error = ProgrammingError("statement", {}, duplicate_error)
        # Manually ensure __cause__ is set for the drill-down logic
        programming_error.__cause__ = duplicate_error

        # Ensure the exception is properly chained for the drill-down logic
        async def raise_programming_error(*args, **kwargs):
            raise programming_error

        mock_service.create_table.side_effect = raise_programming_error

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call create_table with raise_on_duplicate=True (default)
        with pytest.raises(ValueError, match="Table already exists"):
            await create_table(name="test_table", raise_on_duplicate=True)

        # Assert create_table was called
        mock_service.create_table.assert_called_once()

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_create_table_raise_on_duplicate_false_returns_existing(
        self, mock_with_session, mock_table
    ):
        """Test that create_table returns existing table when raise_on_duplicate=False."""
        # Set up the mock service context manager
        mock_service = AsyncMock()

        # Create a chain of exceptions: ProgrammingError wrapping DuplicateTableError
        # SQLAlchemy's ProgrammingError chains exceptions via __cause__
        duplicate_error = DuplicateTableError("relation already exists")
        programming_error = ProgrammingError("statement", {}, duplicate_error)
        # Manually ensure __cause__ is set for the drill-down logic
        programming_error.__cause__ = duplicate_error

        # Ensure the exception is properly chained for the drill-down logic
        async def raise_programming_error(*args, **kwargs):
            raise programming_error

        mock_service.create_table.side_effect = raise_programming_error

        # Mock get_table_by_name to return the existing table
        mock_service.get_table_by_name.return_value = mock_table

        # Mock session.rollback() for the rollback call (must be async)
        mock_service.session = MagicMock()
        mock_service.session.rollback = AsyncMock()

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call create_table with raise_on_duplicate=False
        result = await create_table(name="test_table", raise_on_duplicate=False)

        # Assert create_table was called first
        mock_service.create_table.assert_called_once()

        # Assert rollback was called
        mock_service.session.rollback.assert_called_once()

        # Assert get_table_by_name was called to fetch existing table
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Verify the result is the existing table's dict representation
        assert result == mock_table.to_dict.return_value

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_create_table_raise_on_duplicate_propagates_other_errors(
        self, mock_with_session
    ):
        """Test that create_table propagates non-duplicate errors even with raise_on_duplicate=False."""
        # Set up the mock service context manager
        mock_service = AsyncMock()

        # Create a ProgrammingError with a different cause (not DuplicateTableError)
        other_error = Exception("Some other database error")
        programming_error = ProgrammingError("statement", {}, other_error)
        # Manually ensure __cause__ is set for the drill-down logic
        programming_error.__cause__ = other_error

        # Ensure the exception is properly chained for the drill-down logic
        async def raise_programming_error(*args, **kwargs):
            raise programming_error

        mock_service.create_table.side_effect = raise_programming_error

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call create_table with raise_on_duplicate=False
        # Should still raise the ProgrammingError since it's not a DuplicateTableError
        with pytest.raises(ProgrammingError):
            await create_table(name="test_table", raise_on_duplicate=False)

        # Assert create_table was called
        mock_service.create_table.assert_called_once()


@pytest.mark.anyio
class TestCoreSearchRecords:
    """Test cases for the search_rows UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_search_rows_basic(self, mock_with_session, mock_table, mock_row):
        """Test basic record search without date filters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.search_rows.return_value = [
            mock_row
        ]  # search_rows calls search_rows, not list_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_rows function
        result = await search_rows(
            table="test_table",
            limit=50,
            offset=10,
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert search_rows was called with expected parameters (not list_rows)
        mock_service.search_rows.assert_called_once_with(
            table=mock_table,
            search_term=None,
            start_time=None,
            end_time=None,
            updated_before=None,
            updated_after=None,
            limit=50,
            offset=10,
        )

        # Verify the result
        assert len(result) == 1
        assert result[0]["name"] == mock_row["name"]
        assert result[0]["age"] == mock_row["age"]

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_search_rows_with_date_filters(
        self, mock_with_session, mock_table, mock_row
    ):
        """Test record search with date filtering capabilities."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.search_rows.return_value = [
            mock_row
        ]  # search_rows calls search_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test date filters
        start_time = datetime.now(UTC) - timedelta(days=1)
        end_time = datetime.now(UTC)
        updated_after = datetime.now(UTC) - timedelta(hours=1)
        updated_before = datetime.now(UTC) + timedelta(hours=1)

        # Call the search_rows function with date filters
        result = await search_rows(
            table="test_table",
            limit=50,
            offset=10,
            start_time=start_time,
            end_time=end_time,
            updated_after=updated_after,
            updated_before=updated_before,
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert search_rows was called with date filters
        mock_service.search_rows.assert_called_once_with(
            table=mock_table,
            search_term=None,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
            limit=50,
            offset=10,
        )

        # Verify the result
        assert len(result) == 1
        assert result[0] == mock_row

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_search_rows_limit_validation(self, mock_with_session):
        """Test that search_rows raises ValueError when limit exceeds maximum."""
        from tracecat.config import TRACECAT__MAX_ROWS_CLIENT_POSTGRES

        # Call search_rows with limit exceeding maximum
        with pytest.raises(
            ValueError,
            match=f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}",
        ):
            await search_rows(
                table="test_table",
                limit=TRACECAT__MAX_ROWS_CLIENT_POSTGRES + 1,
            )


@pytest.mark.anyio
class TestCoreInsertRows:
    """Test cases for the insert_rows UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_insert_rows_success(self, mock_with_session, mock_table):
        """Test successful batch row insertion."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.batch_insert_rows.return_value = 3  # Number of rows inserted

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test data
        rows_data = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
            {"name": "Carol", "age": 35},
        ]

        # Call the insert_rows function
        result = await insert_rows(
            table="test_table",
            rows_data=rows_data,
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert batch_insert_rows was called with expected parameters
        mock_service.batch_insert_rows.assert_called_once_with(
            table=mock_table,
            rows=rows_data,
            upsert=False,
        )

        # Verify the result
        assert result == 3


@pytest.mark.anyio
class TestCoreDownloadTable:
    """Test cases for the download_table UDF."""

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_no_format(self, mock_with_session, mock_table):
        """Test downloading table data without format (returns list of dicts)."""
        # Create mock rows with UUID objects (simulating asyncpg UUID type)
        mock_rows = [
            {
                "id": uuid.UUID("123e4567-e89b-12d3-a456-426655440000"),
                "name": "Alice",
                "age": 25,
                "created_at": datetime.now(UTC),
            },
            {
                "id": uuid.UUID("223e4567-e89b-12d3-a456-426655440001"),
                "name": "Bob",
                "age": 30,
                "created_at": datetime.now(UTC),
            },
        ]

        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = mock_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the download_table function
        result = await download(name="test_table", limit=100)

        # Assert service methods were called correctly
        mock_service.get_table_by_name.assert_called_once_with("test_table")
        mock_service.list_rows.assert_called_once_with(table=mock_table, limit=100)

        # Verify the result is a list of dicts with UUIDs converted to strings
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "123e4567-e89b-12d3-a456-426655440000"
        assert result[0]["name"] == "Alice"
        assert result[1]["id"] == "223e4567-e89b-12d3-a456-426655440001"
        assert result[1]["name"] == "Bob"

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_json_format(self, mock_with_session, mock_table):
        """Test downloading table data in JSON format."""
        # Create mock rows with UUID objects
        mock_rows = [
            {
                "id": uuid.UUID("123e4567-e89b-12d3-a456-426655440000"),
                "name": "Alice",
                "age": 25,
            },
        ]

        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = mock_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the download_table function with JSON format
        result = await download(name="test_table", format="json", limit=100)

        # Verify the result is a JSON string
        assert isinstance(result, str)

        # Parse the JSON to verify it's valid and contains the expected data
        parsed = orjson.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "123e4567-e89b-12d3-a456-426655440000"
        assert parsed[0]["name"] == "Alice"
        assert parsed[0]["age"] == 25

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_ndjson_format(self, mock_with_session, mock_table):
        """Test downloading table data in NDJSON format."""
        # Create mock rows with UUID objects
        mock_rows = [
            {
                "id": uuid.UUID("123e4567-e89b-12d3-a456-426655440000"),
                "name": "Alice",
                "age": 25,
            },
            {
                "id": uuid.UUID("223e4567-e89b-12d3-a456-426655440001"),
                "name": "Bob",
                "age": 30,
            },
        ]

        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = mock_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the download_table function with NDJSON format
        result = await download(name="test_table", format="ndjson", limit=100)

        # Verify the result is an NDJSON string
        assert isinstance(result, str)
        lines = result.split("\n")
        assert len(lines) == 2

        # Parse each line to verify it's valid JSON
        parsed_line1 = orjson.loads(lines[0])
        assert parsed_line1["id"] == "123e4567-e89b-12d3-a456-426655440000"
        assert parsed_line1["name"] == "Alice"

        parsed_line2 = orjson.loads(lines[1])
        assert parsed_line2["id"] == "223e4567-e89b-12d3-a456-426655440001"
        assert parsed_line2["name"] == "Bob"

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_csv_format(self, mock_with_session, mock_table):
        """Test downloading table data in CSV format."""
        # Create mock rows with UUID objects
        mock_rows = [
            {
                "id": uuid.UUID("123e4567-e89b-12d3-a456-426655440000"),
                "name": "Alice",
                "age": 25,
            },
            {
                "id": uuid.UUID("223e4567-e89b-12d3-a456-426655440001"),
                "name": "Bob",
                "age": 30,
            },
        ]

        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = mock_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the download_table function with CSV format
        result = await download(name="test_table", format="csv", limit=100)

        # Verify the result is a CSV string
        assert isinstance(result, str)
        # CSV should contain headers and data rows
        assert "id" in result
        assert "name" in result
        assert "age" in result
        assert "Alice" in result
        assert "Bob" in result
        # UUIDs should be converted to strings in the CSV
        assert "123e4567-e89b-12d3-a456-426655440000" in result
        assert "223e4567-e89b-12d3-a456-426655440001" in result

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_markdown_format(self, mock_with_session, mock_table):
        """Test downloading table data in Markdown format."""
        # Create mock rows with UUID objects
        mock_rows = [
            {
                "id": uuid.UUID("123e4567-e89b-12d3-a456-426655440000"),
                "name": "Alice",
                "age": 25,
            },
        ]

        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = mock_rows

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the download_table function with Markdown format
        result = await download(name="test_table", format="markdown", limit=100)

        # Verify the result is a Markdown table string
        assert isinstance(result, str)
        # Markdown tables use pipes
        assert "|" in result
        # Check for content
        assert "id" in result
        assert "name" in result
        assert "age" in result
        assert "Alice" in result
        assert "123e4567-e89b-12d3-a456-426655440000" in result

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_limit_validation(self, mock_with_session):
        """Test that download_table raises ValueError when limit exceeds 1000."""
        # Call download_table with limit exceeding maximum
        with pytest.raises(
            ValueError,
            match="Cannot return more than 1000 rows",
        ):
            await download(
                name="test_table",
                limit=1001,
            )

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_download_table_empty_table(self, mock_with_session, mock_table):
        """Test downloading an empty table."""
        # Set up the mock service context manager with empty rows
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = []

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test with no format (list)
        result = await download(name="empty_table")
        assert result == []

        # Test with JSON format
        result = await download(name="empty_table", format="json")
        assert result == "[]"

        # Test with NDJSON format
        result = await download(name="empty_table", format="ndjson")
        assert result == ""


# =============================================================================
# DDL Integration Test Fixtures
# =============================================================================
# These fixtures are specifically for DDL tests that need to bypass the
# savepoint-based session fixture. DDL operations (CREATE SCHEMA, CREATE TABLE)
# acquire exclusive locks that conflict with SERIALIZABLE isolation.


@pytest.fixture
async def ddl_workspace() -> AsyncGenerator[Workspace, None]:
    """Create a workspace for DDL tests using a clean database session.

    Unlike svc_workspace, this fixture:
    - Creates the workspace directly in the real database (not in a savepoint)
    - Uses READ COMMITTED isolation (default) which works with DDL
    - Properly cleans up the schema after the test

    For pytest-xdist parallel execution, each worker gets a unique workspace ID
    to prevent conflicts between concurrent tests.
    """
    # Generate unique workspace ID - include worker ID for parallel execution
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    workspace_id = WorkspaceUUID(str(uuid.uuid4()))

    workspace = Workspace(
        id=workspace_id,
        name=f"ddl-test-workspace-{worker_id}",
        organization_id=config.TRACECAT__DEFAULT_ORG_ID,
    )

    # Create the workspace in the real database using a clean session
    async with get_async_session_context_manager() as session:
        session.add(workspace)
        await session.commit()
        # Refresh to get the committed state
        await session.refresh(workspace)

    try:
        yield workspace
    finally:
        # Clean up: drop schema first, then delete workspace
        # Use timeout to prevent hanging on database locks during parallel test execution
        schema_name = f"tables_{workspace_id.short()}"
        try:
            async with asyncio.timeout(30):  # 30 second timeout for cleanup
                async with get_async_session_context_manager() as cleanup_session:
                    # Drop the schema if it exists (CASCADE drops all tables in schema)
                    await cleanup_session.execute(
                        text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                    )
                    await cleanup_session.commit()

                    # Delete the workspace
                    await cleanup_session.execute(
                        text("DELETE FROM workspace WHERE id = :workspace_id"),
                        {"workspace_id": str(workspace_id)},
                    )
                    await cleanup_session.commit()
        except TimeoutError:
            # Log but don't fail - the schema/workspace will be orphaned but
            # won't affect other tests since each uses unique IDs
            import logging

            logging.warning(
                f"Timeout during ddl_workspace cleanup for workspace {workspace_id}"
            )


@pytest.fixture
def ddl_role(ddl_workspace: Workspace) -> Role:
    """Create an admin role for DDL tests."""
    return Role(
        type="user",
        access_level=AccessLevel.ADMIN,
        workspace_id=ddl_workspace.id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.mark.anyio
@pytest.mark.xdist_group("ddl")
class TestCoreTableIntegration:
    """Integration tests for core.table UDFs using real database.

    These tests only run with registry_client=False because they execute DDL
    (CREATE SCHEMA, CREATE TABLE) which requires the direct database path.
    DDL cannot work with the savepoint-based session fixture because PostgreSQL
    DDL acquires exclusive locks that conflict with SERIALIZABLE isolation.

    NOTE: These tests are grouped with @pytest.mark.xdist_group("ddl") to run
    serially within the same worker when using pytest-xdist. This prevents
    deadlocks during fixture teardown when multiple workers compete for
    database locks on DDL operations.
    """

    pytestmark = pytest.mark.usefixtures("db")

    @pytest.fixture(autouse=True)
    def skip_registry_client_on(self, registry_client_enabled: bool):
        """Skip these tests when registry_client is enabled."""
        if registry_client_enabled:
            pytest.skip("Integration tests only run with registry_client=False")

    async def test_create_table_with_columns_integration(
        self,
        ddl_workspace: Workspace,
        ddl_role: Role,
    ):
        """Test that create_table UDF actually creates columns in the database.

        This integration test ensures the bug is caught where create_table
        was not creating columns despite them being specified.
        """
        # Set the role context for the UDF
        token = ctx_role.set(ddl_role)
        try:
            # Define columns for the table
            columns = [
                {"name": "username", "type": "TEXT", "nullable": False},
                {"name": "email", "type": "TEXT", "nullable": True},
                {"name": "age", "type": "INTEGER", "nullable": True, "default": 0},
                {"name": "metadata", "type": "JSONB", "nullable": True},
            ]

            # Create table using the UDF (not the service directly)
            result = await create_table(name="integration_test_table", columns=columns)

            # Verify the table was created
            assert result["name"] == "integration_test_table"
            assert "id" in result

            # Now verify the columns were actually created in the database
            # Use TablesService.with_session() to access the same committed data
            async with TablesService.with_session(role=ddl_role) as service:
                tables = await service.list_tables()

                # Find our table
                test_table = None
                for table in tables:
                    if table.name == "integration_test_table":
                        test_table = table
                        break

                assert test_table is not None, "Table was not found in database"

                # Get the table with columns
                table_with_columns = await service.get_table(test_table.id)

                # Verify all columns were created
                assert len(table_with_columns.columns) == 4

                # Check each column
                column_names = {col.name for col in table_with_columns.columns}
                assert "username" in column_names
                assert "email" in column_names
                assert "age" in column_names
                assert "metadata" in column_names

                # Verify column properties
                for col in table_with_columns.columns:
                    if col.name == "username":
                        assert col.type == SqlType.TEXT.value
                        assert col.nullable is False
                    elif col.name == "email":
                        assert col.type == SqlType.TEXT.value
                        assert col.nullable is True
                    elif col.name == "age":
                        assert col.type == SqlType.INTEGER.value
                        assert col.nullable is True
                        assert (
                            col.default == "0"
                        )  # Default values are stored as strings
                    elif col.name == "metadata":
                        assert col.type == SqlType.JSONB.value
                        assert col.nullable is True

            # Test that we can insert data into the table with the created columns
            inserted_row = await insert_row(
                table="integration_test_table",
                row_data={
                    "username": "testuser",
                    "email": "test@example.com",
                    "age": 25,
                    "metadata": {"key": "value"},
                },
            )

            assert inserted_row["username"] == "testuser"
            assert inserted_row["email"] == "test@example.com"
            assert inserted_row["age"] == 25
            assert inserted_row["metadata"] == {"key": "value"}
        finally:
            ctx_role.reset(token)

    async def test_create_table_without_columns_integration(
        self,
        ddl_workspace: Workspace,
        ddl_role: Role,
    ):
        """Test creating a table without predefined columns."""
        # Set the role context for the UDF
        token = ctx_role.set(ddl_role)
        try:
            # Create table without columns
            result = await create_table(name="empty_table")

            # Verify the table was created
            assert result["name"] == "empty_table"
            assert "id" in result

            # Verify in database using the same session type as the UDF
            async with TablesService.with_session(role=ddl_role) as service:
                tables = await service.list_tables()

                table_names = {table.name for table in tables}
                assert "empty_table" in table_names
        finally:
            ctx_role.reset(token)

    async def test_list_tables_integration(
        self,
        ddl_workspace: Workspace,
        ddl_role: Role,
    ):
        """Test listing tables after creation."""
        # Set the role context for the UDF
        token = ctx_role.set(ddl_role)
        try:
            # Create multiple tables
            await create_table(
                name="list_test_1", columns=[{"name": "col1", "type": "TEXT"}]
            )
            await create_table(
                name="list_test_2", columns=[{"name": "col2", "type": "INTEGER"}]
            )

            # List all tables
            tables = await list_tables()

            # Check that our tables are in the list
            table_names = {table["name"] for table in tables}
            assert "list_test_1" in table_names
            assert "list_test_2" in table_names
        finally:
            ctx_role.reset(token)

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_insert_rows_with_upsert(self, mock_with_session, mock_table):
        """Test batch row insertion with upsert enabled."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.batch_insert_rows.return_value = 4  # Number of rows affected

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test data with some updates and some inserts
        rows_data = [
            {"name": "Alice", "age": 26},  # Update
            {"name": "Bob", "age": 31},  # Update
            {"name": "David", "age": 40},  # Insert
            {"name": "Eve", "age": 45},  # Insert
        ]

        # Call the insert_rows function with upsert
        result = await insert_rows(
            table="test_table",
            rows_data=rows_data,
            upsert=True,
        )

        # Assert batch_insert_rows was called with upsert=True
        mock_service.batch_insert_rows.assert_called_once_with(
            table=mock_table,
            rows=rows_data,
            upsert=True,
        )

        # Verify the result
        assert result == 4

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_insert_rows_empty_list(self, mock_with_session, mock_table):
        """Test batch insertion with empty list."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.batch_insert_rows.return_value = 0

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the insert_rows function with empty list
        result = await insert_rows(
            table="test_table",
            rows_data=[],
        )

        # Assert batch_insert_rows was called with empty list
        mock_service.batch_insert_rows.assert_called_once_with(
            table=mock_table,
            rows=[],
            upsert=False,
        )

        # Verify the result
        assert result == 0

    @patch("tracecat_registry.core.table.TablesService.with_session")
    async def test_insert_rows_with_different_columns(
        self, mock_with_session, mock_table
    ):
        """Test batch insertion with rows having different columns."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.batch_insert_rows.return_value = 3

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test data with different columns
        rows_data = [
            {"name": "Alice", "age": 25},  # Has both columns
            {"name": "Bob"},  # Missing age
            {"name": "Carol", "age": 35, "city": "NYC"},  # Extra column
        ]

        # Call the insert_rows function
        result = await insert_rows(
            table="test_table",
            rows_data=rows_data,
            upsert=True,
        )

        # Assert batch_insert_rows was called
        mock_service.batch_insert_rows.assert_called_once_with(
            table=mock_table,
            rows=rows_data,
            upsert=True,
        )

        # Verify the result
        assert result == 3

    async def test_create_table_raise_on_duplicate_false_integration(
        self,
        ddl_workspace: Workspace,
        ddl_role: Role,
    ):
        """Test that create_table with raise_on_duplicate=False returns existing table."""
        # Set the role context for the UDF
        token = ctx_role.set(ddl_role)
        try:
            # Define columns for the table
            columns = [
                {"name": "username", "type": "TEXT", "nullable": False},
                {"name": "email", "type": "TEXT", "nullable": True},
            ]

            # Create table first time
            result1 = await create_table(name="duplicate_test_table", columns=columns)

            # Verify first creation succeeded
            assert result1["name"] == "duplicate_test_table"
            first_table_id = result1["id"]

            # Try to create table again with raise_on_duplicate=False
            result2 = await create_table(
                name="duplicate_test_table", raise_on_duplicate=False
            )

            # Verify it returned the existing table (same ID)
            assert result2["name"] == "duplicate_test_table"
            assert result2["id"] == first_table_id

            # Verify only one table exists with that name
            async with TablesService.with_session(role=ddl_role) as service:
                tables = await service.list_tables()
                duplicate_tables = [
                    t for t in tables if t.name == "duplicate_test_table"
                ]
                assert len(duplicate_tables) == 1
                assert duplicate_tables[0].id == first_table_id
        finally:
            ctx_role.reset(token)

    async def test_create_table_duplicate_with_raise_on_duplicate_true_integration(
        self,
        ddl_workspace: Workspace,
        ddl_role: Role,
    ):
        """Test that create_table raises error on duplicate with raise_on_duplicate=True."""
        # Set the role context for the UDF
        token = ctx_role.set(ddl_role)
        try:
            # Create table first time
            await create_table(name="duplicate_error_test")

            # Try to create table again with raise_on_duplicate=True (default)
            with pytest.raises(ValueError, match="Table already exists"):
                await create_table(name="duplicate_error_test", raise_on_duplicate=True)
        finally:
            ctx_role.reset(token)
