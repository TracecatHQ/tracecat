"""Tests for core.tables UDFs in the registry."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry.core.tables import (
    create_table,
    delete_row,
    insert_row,
    lookup,
    lookup_many,
    search_records,
    update_row,
)

from tracecat.tables.enums import SqlType


@pytest.fixture
def mock_table():
    """Create a mock table for testing."""
    table = MagicMock()
    table.id = uuid.uuid4()
    table.name = "test_table"
    table.model_dump.return_value = {
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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
class TestCoreLookupMany:
    """Test cases for the lookup_many UDF."""

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
    async def test_lookup_many_with_date_filters(self, mock_with_session, mock_row):
        """Test lookup_many with date filtering capabilities."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.lookup_rows.return_value = [mock_row]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test date filters
        start_time = datetime.now(UTC) - timedelta(days=1)
        end_time = datetime.now(UTC)
        updated_after = datetime.now(UTC) - timedelta(hours=1)
        updated_before = datetime.now(UTC) + timedelta(hours=1)

        # Call the lookup_many function with date filters
        result = await lookup_many(
            table="test_table",
            column="name",
            value="John Doe",
            start_time=start_time,
            end_time=end_time,
            updated_after=updated_after,
            updated_before=updated_before,
        )

        # Assert lookup_rows was called with expected parameters including date filters
        mock_service.lookup_rows.assert_called_once()
        call_kwargs = mock_service.lookup_rows.call_args[1]
        assert call_kwargs["table_name"] == "test_table"
        assert call_kwargs["columns"] == ["name"]
        assert call_kwargs["values"] == ["John Doe"]
        assert call_kwargs["start_time"] == start_time
        assert call_kwargs["end_time"] == end_time
        assert call_kwargs["updated_after"] == updated_after
        assert call_kwargs["updated_before"] == updated_before

        # Verify the result
        assert result == [mock_row]


@pytest.mark.anyio
class TestCoreInsertRow:
    """Test cases for the insert_row UDF."""

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

        # Verify the result
        assert result == mock_table.model_dump.return_value

    @patch("tracecat_registry.core.tables.TablesService.with_session")
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

        # Verify the result
        assert result == mock_table.model_dump.return_value


@pytest.mark.anyio
class TestCoreSearchRecords:
    """Test cases for the search_records UDF."""

    @patch("tracecat_registry.core.tables.TablesService.with_session")
    async def test_search_records_basic(self, mock_with_session, mock_table, mock_row):
        """Test basic record search without date filters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service.list_rows.return_value = [mock_row]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_records function
        result = await search_records(
            table="test_table",
            limit=50,
            offset=10,
        )

        # Assert get_table_by_name was called
        mock_service.get_table_by_name.assert_called_once_with("test_table")

        # Assert list_rows was called with expected parameters
        mock_service.list_rows.assert_called_once_with(mock_table, limit=50, offset=10)

        # Verify the result
        assert len(result) == 1
        assert result[0]["name"] == mock_row["name"]
        assert result[0]["age"] == mock_row["age"]

    @patch("tracecat_registry.core.tables.TablesService.with_session")
    async def test_search_records_with_date_filters(
        self, mock_with_session, mock_table, mock_row
    ):
        """Test record search with date filtering capabilities."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_table_by_name.return_value = mock_table
        mock_service._get_schema_name.return_value = "test_schema"
        mock_service._sanitize_identifier.return_value = "test_table"

        # Mock the connection and execute
        mock_connection = AsyncMock()
        mock_result = MagicMock()  # Use MagicMock instead of AsyncMock
        mock_mappings = MagicMock()  # Use MagicMock instead of AsyncMock
        mock_mappings.all.return_value = [mock_row]
        mock_result.mappings.return_value = mock_mappings
        mock_connection.execute.return_value = mock_result

        # Mock the async connection method
        async def mock_connection_coro():
            return mock_connection

        mock_service.session.connection = mock_connection_coro

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Test date filters
        start_time = datetime.now(UTC) - timedelta(days=1)
        end_time = datetime.now(UTC)
        updated_after = datetime.now(UTC) - timedelta(hours=1)
        updated_before = datetime.now(UTC) + timedelta(hours=1)

        # Call the search_records function with date filters
        result = await search_records(
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

        # Assert the SQL execution was called instead of list_rows
        mock_connection.execute.assert_called_once()

        # Verify the result
        assert len(result) == 1
        assert result[0] == mock_row

    @patch("tracecat_registry.core.tables.TablesService.with_session")
    async def test_search_records_limit_validation(self, mock_with_session):
        """Test that search_records raises ValueError when limit exceeds maximum."""
        from tracecat.config import TRACECAT__MAX_ROWS_CLIENT_POSTGRES

        # Call search_records with limit exceeding maximum
        with pytest.raises(
            ValueError,
            match=f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}",
        ):
            await search_records(
                table="test_table",
                limit=TRACECAT__MAX_ROWS_CLIENT_POSTGRES + 1,
            )
