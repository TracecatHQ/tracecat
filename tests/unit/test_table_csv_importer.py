"""Unit tests for the CSVImporter class."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tracecat.db.schemas import Table, TableColumn
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import ColumnInfo, CSVImporter
from tracecat.tables.service import TablesService
from tracecat.types.exceptions import TracecatImportError


@pytest.fixture
def table_columns() -> list[TableColumn]:
    """Fixture to create sample table columns for testing."""
    table_id = uuid4()
    return [
        TableColumn(
            id=uuid4(),
            table_id=table_id,
            name="name",
            type=SqlType.TEXT,
            nullable=True,
            default=None,
        ),
        TableColumn(
            id=uuid4(),
            table_id=table_id,
            name="age",
            type=SqlType.INTEGER,
            nullable=True,
            default=None,
        ),
        TableColumn(
            id=uuid4(),
            table_id=table_id,
            name="active",
            type=SqlType.BOOLEAN,
            nullable=True,
            default=None,
        ),
        TableColumn(
            id=uuid4(),
            table_id=table_id,
            name="score",
            type=SqlType.DECIMAL,
            nullable=True,
            default=None,
        ),
        TableColumn(
            id=uuid4(),
            table_id=table_id,
            name="data",
            type=SqlType.JSONB,
            nullable=True,
            default=None,
        ),
    ]


@pytest.fixture
def csv_importer(table_columns: list[TableColumn]) -> CSVImporter:
    """Fixture to create a CSVImporter instance."""
    return CSVImporter(table_columns=table_columns)


class TestCSVImporter:
    """Test suite for CSVImporter class."""

    def test_init(self, table_columns: list[TableColumn]) -> None:
        """Test CSVImporter initialization."""
        importer = CSVImporter(table_columns=table_columns)

        assert importer.chunk_size == 1000
        assert importer.total_rows_inserted == 0
        assert len(importer.columns) == 5

        # Verify column info mappings
        assert all(
            isinstance(col_info, ColumnInfo) for col_info in importer.columns.values()
        )
        assert importer.columns["name"].type == SqlType.TEXT
        assert importer.columns["age"].type == SqlType.INTEGER
        assert importer.columns["active"].type == SqlType.BOOLEAN
        assert importer.columns["score"].type == SqlType.DECIMAL

    def test_convert_value_empty(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with empty values."""
        assert csv_importer.convert_value("", SqlType.TEXT) == ""
        assert csv_importer.convert_value("", SqlType.INTEGER) == ""
        assert csv_importer.convert_value("", SqlType.BOOLEAN) == ""

    def test_convert_value_text(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with text values."""
        assert csv_importer.convert_value("hello", SqlType.TEXT) == "hello"

    def test_convert_value_integer(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with integer values."""
        assert csv_importer.convert_value("123", SqlType.INTEGER) == 123
        assert csv_importer.convert_value("-456", SqlType.INTEGER) == -456
        with pytest.raises(
            TracecatImportError, match="Cannot convert value '12.34' to SqlType INTEGER"
        ):
            csv_importer.convert_value("12.34", SqlType.INTEGER)

    def test_convert_value_decimal(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with decimal values."""
        assert csv_importer.convert_value("123.45", SqlType.DECIMAL) == 123.45
        assert csv_importer.convert_value("-456.78", SqlType.DECIMAL) == -456.78
        with pytest.raises(
            TracecatImportError, match="Cannot convert value 'abc' to SqlType DECIMAL"
        ):
            csv_importer.convert_value("abc", SqlType.DECIMAL)

    def test_convert_value_boolean(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with boolean values."""
        assert csv_importer.convert_value("true", SqlType.BOOLEAN) is True
        assert csv_importer.convert_value("false", SqlType.BOOLEAN) is False
        assert csv_importer.convert_value("1", SqlType.BOOLEAN) is True
        assert csv_importer.convert_value("0", SqlType.BOOLEAN) is False
        with pytest.raises(
            TracecatImportError,
            match="Cannot convert value 'invalid' to SqlType BOOLEAN",
        ):
            csv_importer.convert_value("invalid", SqlType.BOOLEAN)

    def test_convert_value_jsonb(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with JSONB values."""
        # Valid JSON string
        assert (
            csv_importer.convert_value('{"key": "value"}', SqlType.JSONB)
            == '{"key": "value"}'
        )

        # Python dict that gets converted to JSON string
        assert (
            csv_importer.convert_value({"key": "value"}, SqlType.JSONB)
            == '{"key": "value"}'
        )

        # Empty values
        assert csv_importer.convert_value("", SqlType.JSONB) == ""
        assert csv_importer.convert_value(None, SqlType.JSONB) is None

        # Invalid JSON string
        with pytest.raises(TracecatImportError, match="Invalid JSON format"):
            csv_importer.convert_value("{invalid_json}", SqlType.JSONB)

        # Unconvertible object (like a custom class)
        class CustomClass:
            pass

        with pytest.raises(TracecatImportError, match="Cannot convert value to JSON"):
            csv_importer.convert_value(CustomClass(), SqlType.JSONB)

    def test_map_row(self, csv_importer: CSVImporter) -> None:
        """Test mapping CSV rows to table columns."""
        csv_row = {
            "csv_name": "John Doe",
            "csv_age": "30",
            "csv_active": "true",
            "csv_score": "95.5",
            "extra_col": "ignored",
        }

        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
            "csv_active": "active",
            "csv_score": "score",
            "extra_col": "skip",
        }

        mapped_row = csv_importer.map_row(csv_row, column_mapping)

        assert mapped_row == {
            "name": "John Doe",
            "age": 30,
            "active": True,
            "score": 95.5,
        }

    def test_map_row_with_empty_values(self, csv_importer: CSVImporter) -> None:
        """Test mapping CSV rows with empty values."""
        csv_row = {
            "csv_name": "",
            "csv_age": "",
            "csv_active": "",
            "csv_score": "",
        }

        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
            "csv_active": "active",
            "csv_score": "score",
        }

        mapped_row = csv_importer.map_row(csv_row, column_mapping)

        assert mapped_row == {
            "name": "",
            "age": "",
            "active": "",
            "score": "",
        }

    def test_map_row_with_invalid_mapping(self, csv_importer: CSVImporter) -> None:
        """Test mapping CSV rows with invalid column mapping."""
        csv_row = {"csv_name": "John Doe"}
        column_mapping = {"csv_name": "invalid_column"}

        mapped_row = csv_importer.map_row(csv_row, column_mapping)
        assert mapped_row == {}

    @pytest.mark.anyio
    async def test_process_chunk(self, csv_importer: CSVImporter) -> None:
        """Test processing a chunk of rows."""
        # Create mock service and table
        mock_service = AsyncMock(spec=TablesService)
        mock_service.batch_insert_rows = AsyncMock(return_value=2)
        mock_table = Mock(spec=Table)

        # Test data
        chunk = [
            {"name": "John", "age": 30},
            {"name": "Jane", "age": 25},
        ]

        await csv_importer.process_chunk(chunk, mock_service, mock_table)

        # Verify service was called correctly
        mock_service.batch_insert_rows.assert_called_once_with(mock_table, chunk)
        assert csv_importer.total_rows_inserted == 2

    @pytest.mark.anyio
    async def test_process_empty_chunk(self, csv_importer: CSVImporter) -> None:
        """Test processing an empty chunk."""
        mock_service = AsyncMock(spec=TablesService)
        mock_table = Mock(spec=Table)

        await csv_importer.process_chunk([], mock_service, mock_table)

        mock_service.batch_insert_rows.assert_not_called()
        assert csv_importer.total_rows_inserted == 0
