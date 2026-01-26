"""Tests for core.table UDFs in the registry.

These tests verify the UDF layer behavior by mocking the SDK client context.
For end-to-end integration tests, see test_table_characterization.py.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import tracecat_registry.core.table as table_core
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


@pytest.fixture
def mock_tables_client():
    """Create a mock tables client for SDK path testing."""
    return AsyncMock()


@pytest.fixture(autouse=True)
def mock_get_context(mock_tables_client: AsyncMock):
    """Mock get_context to return a fake context with mock tables client."""
    fake_ctx = SimpleNamespace(tables=mock_tables_client)
    with patch.object(table_core, "get_context", return_value=fake_ctx):
        yield


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

    async def test_lookup_success(self, mock_tables_client: AsyncMock, mock_row):
        """Test successful single row lookup."""
        mock_tables_client.lookup.return_value = mock_row

        result = await lookup(
            table="test_table",
            column="name",
            value="John Doe",
        )

        mock_tables_client.lookup.assert_called_once_with(
            table="test_table",
            column="name",
            value="John Doe",
        )
        assert result == mock_row

    async def test_lookup_not_found(self, mock_tables_client: AsyncMock):
        """Test lookup when no row is found."""
        mock_tables_client.lookup.return_value = None

        result = await lookup(
            table="test_table",
            column="name",
            value="Nonexistent",
        )

        assert result is None


@pytest.mark.anyio
class TestCoreIsInTable:
    """Test cases for the is_in_table UDF."""

    async def test_is_in_table_true(self, mock_tables_client: AsyncMock):
        """Returns True when at least one matching row exists."""
        mock_tables_client.exists.return_value = True

        result = await is_in(
            table="test_table",
            column="name",
            value="John Doe",
        )

        mock_tables_client.exists.assert_called_once_with(
            table="test_table",
            column="name",
            value="John Doe",
        )
        assert result is True

    async def test_is_in_table_false(self, mock_tables_client: AsyncMock):
        """Returns False when no matching row exists."""
        mock_tables_client.exists.return_value = False

        result = await is_in(
            table="test_table",
            column="name",
            value="Nonexistent",
        )

        assert result is False


@pytest.mark.anyio
class TestCoreLookupMany:
    """Test cases for the lookup_many UDF."""

    async def test_lookup_many_success(self, mock_tables_client: AsyncMock, mock_row):
        """Test successful multiple row lookup."""
        mock_rows = [mock_row, {**mock_row, "id": str(uuid.uuid4()), "age": 25}]
        mock_tables_client.lookup_many.return_value = mock_rows

        result = await lookup_many(
            table="test_table",
            column="name",
            value="John Doe",
            limit=50,
        )

        mock_tables_client.lookup_many.assert_called_once_with(
            table="test_table",
            column="name",
            value="John Doe",
            limit=50,
        )
        assert result == mock_rows


@pytest.mark.anyio
class TestCoreInsertRow:
    """Test cases for the insert_row UDF."""

    async def test_insert_row_success(self, mock_tables_client: AsyncMock, mock_row):
        """Test successful row insertion."""
        mock_tables_client.insert_row.return_value = mock_row

        result = await insert_row(
            table="test_table",
            row_data={"name": "John Doe", "age": 30},
        )

        mock_tables_client.insert_row.assert_called_once_with(
            table="test_table",
            row_data={"name": "John Doe", "age": 30},
            upsert=False,
        )
        assert result == mock_row

    async def test_insert_row_with_upsert(
        self, mock_tables_client: AsyncMock, mock_row
    ):
        """Test row insertion with upsert enabled."""
        mock_tables_client.insert_row.return_value = mock_row

        result = await insert_row(
            table="test_table",
            row_data={"name": "John Doe", "age": 30},
            upsert=True,
        )

        mock_tables_client.insert_row.assert_called_once_with(
            table="test_table",
            row_data={"name": "John Doe", "age": 30},
            upsert=True,
        )
        assert result == mock_row


@pytest.mark.anyio
class TestCoreUpdateRow:
    """Test cases for the update_row UDF."""

    async def test_update_row_success(self, mock_tables_client: AsyncMock, mock_row):
        """Test successful row update."""
        updated_row = {**mock_row, "age": 31}
        mock_tables_client.update_row.return_value = updated_row

        result = await update_row(
            table="test_table",
            row_id=mock_row["id"],
            row_data={"age": 31},
        )

        mock_tables_client.update_row.assert_called_once_with(
            table="test_table",
            row_id=mock_row["id"],
            row_data={"age": 31},
        )
        assert result == updated_row


@pytest.mark.anyio
class TestCoreDeleteRow:
    """Test cases for the delete_row UDF."""

    async def test_delete_row_success(self, mock_tables_client: AsyncMock, mock_row):
        """Test successful row deletion."""
        mock_tables_client.delete_row.return_value = None

        await delete_row(
            table="test_table",
            row_id=mock_row["id"],
        )

        mock_tables_client.delete_row.assert_called_once_with(
            table="test_table",
            row_id=mock_row["id"],
        )


@pytest.mark.anyio
class TestCoreCreateTable:
    """Test cases for the create_table UDF."""

    async def test_create_table_without_columns(self, mock_tables_client: AsyncMock):
        """Test table creation without predefined columns."""
        mock_result = {"id": str(uuid.uuid4()), "name": "test_table"}
        mock_tables_client.create_table.return_value = mock_result

        result = await create_table(name="test_table")

        # columns is only passed when not None
        mock_tables_client.create_table.assert_called_once_with(
            name="test_table",
            raise_on_duplicate=True,
        )
        assert result == mock_result

    async def test_create_table_with_columns(self, mock_tables_client: AsyncMock):
        """Test table creation with predefined columns."""
        columns = [
            {"name": "name", "type": "TEXT", "nullable": True, "default": None},
            {"name": "age", "type": "INTEGER", "nullable": False, "default": 0},
        ]
        mock_result = {"id": str(uuid.uuid4()), "name": "test_table"}
        mock_tables_client.create_table.return_value = mock_result

        result = await create_table(name="test_table", columns=columns)

        mock_tables_client.create_table.assert_called_once_with(
            name="test_table",
            columns=columns,
            raise_on_duplicate=True,
        )
        assert result == mock_result

    async def test_create_table_raise_on_duplicate_false(
        self, mock_tables_client: AsyncMock
    ):
        """Test table creation with raise_on_duplicate=False."""
        mock_result = {"id": str(uuid.uuid4()), "name": "test_table"}
        mock_tables_client.create_table.return_value = mock_result

        result = await create_table(name="test_table", raise_on_duplicate=False)

        # columns is only passed when not None
        mock_tables_client.create_table.assert_called_once_with(
            name="test_table",
            raise_on_duplicate=False,
        )
        assert result == mock_result


@pytest.mark.anyio
class TestCoreListTables:
    """Test cases for the list_tables UDF."""

    async def test_list_tables_success(self, mock_tables_client: AsyncMock):
        """Test successful table listing."""
        mock_tables = [
            {"id": str(uuid.uuid4()), "name": "table1"},
            {"id": str(uuid.uuid4()), "name": "table2"},
        ]
        mock_tables_client.list_tables.return_value = mock_tables

        result = await list_tables()

        mock_tables_client.list_tables.assert_called_once()
        assert result == mock_tables


@pytest.mark.anyio
class TestCoreGetTableMetadata:
    """Test cases for the get_table_metadata UDF."""

    async def test_get_table_metadata_success(self, mock_tables_client: AsyncMock):
        """Test successful table metadata retrieval."""
        mock_metadata = {
            "id": str(uuid.uuid4()),
            "name": "test_table",
            "columns": [{"name": "col1", "type": "TEXT"}],
        }
        mock_tables_client.get_table_metadata.return_value = mock_metadata

        result = await get_table_metadata(name="test_table")

        # Implementation passes name as positional argument
        mock_tables_client.get_table_metadata.assert_called_once_with("test_table")
        assert result == mock_metadata


@pytest.mark.anyio
class TestCoreSearchRecords:
    """Test cases for the search_rows UDF."""

    async def test_search_rows_basic(self, mock_tables_client: AsyncMock, mock_row):
        """Test basic record search without date filters."""
        mock_tables_client.search_rows.return_value = [mock_row]

        result = await search_rows(
            table="test_table",
            limit=50,
            offset=10,
        )

        # Only non-None parameters are passed to the client
        mock_tables_client.search_rows.assert_called_once_with(
            table="test_table",
            limit=50,
            offset=10,
        )
        assert len(result) == 1
        assert result[0] == mock_row

    async def test_search_rows_with_date_filters(
        self, mock_tables_client: AsyncMock, mock_row
    ):
        """Test record search with date filtering capabilities."""
        mock_tables_client.search_rows.return_value = [mock_row]

        start_time = datetime.now(UTC) - timedelta(days=1)
        end_time = datetime.now(UTC)
        updated_after = datetime.now(UTC) - timedelta(hours=1)
        updated_before = datetime.now(UTC) + timedelta(hours=1)

        result = await search_rows(
            table="test_table",
            limit=50,
            offset=10,
            start_time=start_time,
            end_time=end_time,
            updated_after=updated_after,
            updated_before=updated_before,
        )

        # Only non-None parameters are passed to the client
        mock_tables_client.search_rows.assert_called_once_with(
            table="test_table",
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
            limit=50,
            offset=10,
        )
        assert len(result) == 1
        assert result[0] == mock_row


@pytest.mark.anyio
class TestCoreInsertRows:
    """Test cases for the insert_rows UDF."""

    async def test_insert_rows_success(self, mock_tables_client: AsyncMock):
        """Test successful batch row insertion."""
        mock_tables_client.insert_rows.return_value = 3

        rows_data = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
            {"name": "Carol", "age": 35},
        ]

        result = await insert_rows(
            table="test_table",
            rows_data=rows_data,
        )

        mock_tables_client.insert_rows.assert_called_once_with(
            table="test_table",
            rows_data=rows_data,
            upsert=False,
        )
        assert result == 3

    async def test_insert_rows_with_upsert(self, mock_tables_client: AsyncMock):
        """Test batch row insertion with upsert enabled."""
        mock_tables_client.insert_rows.return_value = 4

        rows_data = [
            {"name": "Alice", "age": 26},
            {"name": "Bob", "age": 31},
        ]

        result = await insert_rows(
            table="test_table",
            rows_data=rows_data,
            upsert=True,
        )

        mock_tables_client.insert_rows.assert_called_once_with(
            table="test_table",
            rows_data=rows_data,
            upsert=True,
        )
        assert result == 4


@pytest.mark.anyio
class TestCoreDownloadTable:
    """Test cases for the download_table UDF."""

    async def test_download_table_no_format(self, mock_tables_client: AsyncMock):
        """Test downloading table data without format (returns list of dicts)."""
        mock_rows = [
            {"id": "123e4567-e89b-12d3-a456-426655440000", "name": "Alice", "age": 25},
            {"id": "223e4567-e89b-12d3-a456-426655440001", "name": "Bob", "age": 30},
        ]
        mock_tables_client.download.return_value = mock_rows

        result = await download(name="test_table", limit=100)

        # Only non-None parameters are passed to the client
        mock_tables_client.download.assert_called_once_with(
            table="test_table",
            limit=100,
        )
        assert isinstance(result, list)
        assert len(result) == 2

    async def test_download_table_json_format(self, mock_tables_client: AsyncMock):
        """Test downloading table data in JSON format."""
        mock_json = '[{"id": "123", "name": "Alice"}]'
        mock_tables_client.download.return_value = mock_json

        result = await download(name="test_table", format="json", limit=100)

        mock_tables_client.download.assert_called_once_with(
            table="test_table",
            format="json",
            limit=100,
        )
        assert isinstance(result, str)

    async def test_download_table_csv_format(self, mock_tables_client: AsyncMock):
        """Test downloading table data in CSV format."""
        mock_csv = "id,name\n123,Alice"
        mock_tables_client.download.return_value = mock_csv

        result = await download(name="test_table", format="csv", limit=100)

        mock_tables_client.download.assert_called_once_with(
            table="test_table",
            format="csv",
            limit=100,
        )
        assert isinstance(result, str)
