"""Unit tests for the CSVImporter class."""

from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tracecat.db.models import Table, TableColumn
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import ColumnInfo, CSVImporter, load_csv_table
from tracecat.tables.service import TablesService


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
            type=SqlType.NUMERIC,
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
        assert len(importer.columns) == 4

        # Verify column info mappings
        assert all(
            isinstance(col_info, ColumnInfo) for col_info in importer.columns.values()
        )
        assert importer.columns["name"].type == SqlType.TEXT
        assert importer.columns["age"].type == SqlType.INTEGER
        assert importer.columns["active"].type == SqlType.BOOLEAN
        assert importer.columns["score"].type == SqlType.NUMERIC

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
            TypeError, match="Cannot convert value '12.34' to SqlType INTEGER"
        ):
            csv_importer.convert_value("12.34", SqlType.INTEGER)

    def test_convert_value_numeric(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with numeric values."""
        assert csv_importer.convert_value("123.45", SqlType.NUMERIC) == 123.45
        assert csv_importer.convert_value("-456.78", SqlType.NUMERIC) == -456.78
        with pytest.raises(
            TypeError, match="Cannot convert value 'abc' to SqlType NUMERIC"
        ):
            csv_importer.convert_value("abc", SqlType.NUMERIC)

    def test_convert_value_boolean(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with boolean values."""
        assert csv_importer.convert_value("true", SqlType.BOOLEAN) is True
        assert csv_importer.convert_value("false", SqlType.BOOLEAN) is False
        assert csv_importer.convert_value("1", SqlType.BOOLEAN) is True
        assert csv_importer.convert_value("0", SqlType.BOOLEAN) is False
        with pytest.raises(
            TypeError, match="Cannot convert value 'invalid' to SqlType BOOLEAN"
        ):
            csv_importer.convert_value("invalid", SqlType.BOOLEAN)

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


class TestLoadCSVTable:
    def test_load_csv_table_basic(self) -> None:
        csv_text = "Name,Age,Score\nAlice,30,85.5\nBob,25,91\n"
        columns, rows = load_csv_table(StringIO(csv_text))

        assert [column.name for column in columns] == ["name", "age", "score"]
        assert [column.type for column in columns] == [
            SqlType.TEXT,
            SqlType.INTEGER,
            SqlType.NUMERIC,
        ]
        assert rows == [
            {"name": "Alice", "age": 30, "score": 85.5},
            {"name": "Bob", "age": 25, "score": 91.0},
        ]

    def test_load_csv_table_mixed_types(self) -> None:
        csv_text = "Value\n1\nfoo\n3\n"
        columns, rows = load_csv_table(StringIO(csv_text))

        assert columns[0].name == "value"
        assert columns[0].type == SqlType.TEXT
        assert rows == [
            {"value": 1},
            {"value": "foo"},
            {"value": 3},
        ]

    def test_load_csv_table_sanitises_columns(self) -> None:
        csv_text = "123 Invalid!,123 Invalid!,Flag\ntrue,false,true\n"
        columns, rows = load_csv_table(StringIO(csv_text))

        assert [column.name for column in columns] == [
            "col_123_invalid",
            "col_123_invalid_1",
            "flag",
        ]
        assert [column.type for column in columns] == [
            SqlType.BOOLEAN,
            SqlType.BOOLEAN,
            SqlType.BOOLEAN,
        ]
        assert rows == [
            {
                "col_123_invalid": True,
                "col_123_invalid_1": False,
                "flag": True,
            }
        ]
