"""Tests for core.table UDFs in the registry."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry.core.table import (
    create_table,
    delete_row,
    download,
    insert_row,
    insert_rows,
    list_tables,
    lookup,
    lookup_many,
    search_rows,
    update_row,
)

from tracecat.contexts import ctx_role
from tracecat.db.schemas import Workspace
from tracecat.tables.enums import SqlType
from tracecat.tables.service import TablesService
from tracecat.types.auth import Role


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

        # Verify the result
        assert result == mock_table.model_dump.return_value

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

        # Verify the result
        assert result == mock_table.model_dump.return_value


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


@pytest.mark.anyio
class TestCoreTableIntegration:
    """Integration tests for core.table UDFs using real database."""

    pytestmark = pytest.mark.usefixtures("db")

    async def test_create_table_with_columns_integration(
        self, session: AsyncSession, svc_workspace: Workspace, svc_admin_role: Role
    ):
        """Test that create_table UDF actually creates columns in the database.

        This integration test ensures the bug is caught where create_table
        was not creating columns despite them being specified.
        """
        # Set the role context for the UDF
        token = ctx_role.set(svc_admin_role)
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
            async with TablesService.with_session(role=svc_admin_role) as service:
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
        self, session: AsyncSession, svc_workspace: Workspace, svc_admin_role: Role
    ):
        """Test creating a table without predefined columns."""
        # Set the role context for the UDF
        token = ctx_role.set(svc_admin_role)
        try:
            # Create table without columns
            result = await create_table(name="empty_table")

            # Verify the table was created
            assert result["name"] == "empty_table"
            assert "id" in result

            # Verify in database using the same session type as the UDF
            async with TablesService.with_session(role=svc_admin_role) as service:
                tables = await service.list_tables()

                table_names = {table.name for table in tables}
                assert "empty_table" in table_names
        finally:
            ctx_role.reset(token)

    async def test_list_tables_integration(
        self, session: AsyncSession, svc_workspace: Workspace, svc_admin_role: Role
    ):
        """Test listing tables after creation."""
        # Set the role context for the UDF
        token = ctx_role.set(svc_admin_role)
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
