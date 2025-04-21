"""Unit tests for the CSVImporter class."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import polars as pl
import pytest

from tracecat.db.schemas import Table, TableColumn

# Import the new classes
from tracecat.tables.csv_table import SchemaInferenceService
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import ColumnInfo, CSVImporter, TracecatImportError
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
            type=SqlType.DECIMAL,
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
        # Update the match to expect the generic message
        with pytest.raises(TracecatImportError, match="Cannot convert value"):
            csv_importer.convert_value("12.34", SqlType.INTEGER)

    def test_convert_value_decimal(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with decimal values."""
        assert csv_importer.convert_value("123.45", SqlType.DECIMAL) == 123.45
        assert csv_importer.convert_value("-456.78", SqlType.DECIMAL) == -456.78
        # Update the match to expect the generic message
        with pytest.raises(TracecatImportError, match="Cannot convert value"):
            csv_importer.convert_value("abc", SqlType.DECIMAL)

    def test_convert_value_boolean(self, csv_importer: CSVImporter) -> None:
        """Test convert_value with boolean values."""
        assert csv_importer.convert_value("true", SqlType.BOOLEAN) is True
        assert csv_importer.convert_value("false", SqlType.BOOLEAN) is False
        assert csv_importer.convert_value("1", SqlType.BOOLEAN) is True
        assert csv_importer.convert_value("0", SqlType.BOOLEAN) is False
        # Update the match to expect the generic message
        with pytest.raises(TracecatImportError, match="Cannot convert value"):
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


@pytest.fixture
def sample_row_data():
    """Fixture to provide sample row data for testing schema inference."""
    return {
        "text_col": "sample text",
        "int_col": 42,
        "float_col": 3.14,
        "bool_col": True,
        "null_col": None,
        "json_col": {"key": "value"},
    }


class TestSchemaInferenceService:
    """Test suite for SchemaInferenceService class."""

    def test_init_with_data(self, sample_row_data):
        """Test initialization with sample data."""
        service = SchemaInferenceService(sample_row_data)

        assert service.sample_data == sample_row_data
        assert service._inferred_columns is not None

        # Verify that schema was inferred
        inferred = service.get_inferred_columns()
        assert (
            len(inferred) == 6
        )  # Should match the number of columns in sample_row_data

        # Check if columns were correctly identified
        column_types = {col.name: col.type for col in inferred}
        assert column_types["text_col"] == SqlType.TEXT
        assert column_types["int_col"] == SqlType.INTEGER
        assert column_types["float_col"] == SqlType.DECIMAL
        assert column_types["bool_col"] == SqlType.BOOLEAN
        assert column_types["null_col"] == SqlType.TEXT  # Null defaults to TEXT
        assert column_types["json_col"] == SqlType.JSONB

    def test_init_without_data(self):
        """Test initialization without sample data."""
        service = SchemaInferenceService()

        assert service.sample_data is None
        assert service._inferred_columns is None

        # Verify empty results when no data provided
        assert service.get_inferred_columns() == []

    def test_map_polars_types(self):
        """Test mapping of Polars types to SQL types."""
        service = SchemaInferenceService()

        # Test direct mappings
        assert service._map_polars_type(pl.Utf8()) == SqlType.TEXT
        assert service._map_polars_type(pl.Int64()) == SqlType.INTEGER
        assert service._map_polars_type(pl.Float64()) == SqlType.DECIMAL
        assert service._map_polars_type(pl.Boolean()) == SqlType.BOOLEAN

        # Test other numeric types
        assert service._map_polars_type(pl.Int32()) == SqlType.INTEGER
        assert service._map_polars_type(pl.Int16()) == SqlType.INTEGER
        assert service._map_polars_type(pl.Int8()) == SqlType.INTEGER
        assert service._map_polars_type(pl.UInt64()) == SqlType.INTEGER
        assert service._map_polars_type(pl.Float32()) == SqlType.DECIMAL

        # Test default fallback
        class UnknownType:
            pass

        assert service._map_polars_type(UnknownType()) == SqlType.TEXT

    def test_get_inferred_columns_triggers_inference(self):
        """Test that get_inferred_columns triggers inference if needed."""
        service = SchemaInferenceService()
        service.sample_data = {"text": "sample"}
        service._inferred_columns = None

        # Should trigger _infer_schema
        columns = service.get_inferred_columns()

        assert len(columns) == 1
        assert columns[0].name == "text"
        assert columns[0].type == SqlType.TEXT
        assert columns[0].sample_value == "sample"

    def test_complex_data_inference(self):
        """Test inference with more complex data types."""
        data = {
            "date_string": "2023-01-01",  # Should be TEXT, not date
            "mixed_numbers": "123.45abc",  # Should be TEXT, not number
            "large_int": 9223372036854775807,  # Max int64
            "scientific": 1.23e10,  # Scientific notation float
            "empty_string": "",
        }

        service = SchemaInferenceService(data)
        inferred = service.get_inferred_columns()

        column_types = {col.name: col.type for col in inferred}
        assert column_types["date_string"] == SqlType.TEXT
        assert column_types["mixed_numbers"] == SqlType.TEXT
        assert column_types["large_int"] == SqlType.INTEGER
        assert column_types["scientific"] == SqlType.DECIMAL
        assert column_types["empty_string"] == SqlType.TEXT
