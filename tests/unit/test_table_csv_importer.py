"""Unit tests for the CSVImporter class."""

from collections.abc import Mapping
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tracecat.db.models import Table, TableColumn
from tracecat.exceptions import TracecatImportError
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import ColumnInfo, CSVImporter, CSVSchemaInferer
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
        assert csv_importer.convert_value("", SqlType.INTEGER) is None
        assert csv_importer.convert_value("", SqlType.BOOLEAN) is None

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
            "age": None,
            "active": None,
            "score": None,
        }

    def test_map_row_strips_non_text_values(self, csv_importer: CSVImporter) -> None:
        """Test mapping trims whitespace for non-text columns."""
        csv_row = {
            "csv_name": "  John Doe  ",
            "csv_age": " 30 ",
            "csv_active": " true ",
            "csv_score": " 95.5 ",
        }

        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
            "csv_active": "active",
            "csv_score": "score",
        }

        mapped_row = csv_importer.map_row(csv_row, column_mapping)

        assert mapped_row == {
            "name": "  John Doe  ",
            "age": 30,
            "active": True,
            "score": 95.5,
        }

    def test_map_row_with_invalid_mapping(self, csv_importer: CSVImporter) -> None:
        """Test mapping CSV rows with invalid column mapping."""
        csv_row = {"csv_name": "John Doe"}
        column_mapping = {"csv_name": "invalid_column"}

        mapped_row = csv_importer.map_row(csv_row, column_mapping)
        assert mapped_row == {}

    def test_map_row_with_bom_prefixed_header(self, csv_importer: CSVImporter) -> None:
        """Test mapping still works when the first CSV header contains a BOM."""
        csv_row = {
            "\ufeffcsv_name": "John Doe",
            "csv_age": "30",
        }
        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
        }

        mapped_row = csv_importer.map_row(csv_row, column_mapping, row_number=2)

        assert mapped_row == {
            "name": "John Doe",
            "age": 30,
        }

    def test_map_row_with_missing_csv_header_raises(
        self, csv_importer: CSVImporter
    ) -> None:
        """Test missing CSV headers raise a user-friendly import error."""
        csv_row = {"csv_age": "30"}
        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
        }

        with pytest.raises(
            TracecatImportError,
            match=(
                "Mapped CSV column 'csv_name' was not found in file headers"
                " at CSV row 2"
            ),
        ):
            csv_importer.map_row(csv_row, column_mapping, row_number=2)

    def test_map_row_with_null_byte_raises(self, csv_importer: CSVImporter) -> None:
        """Test null bytes in CSV content raise a user-friendly import error."""
        csv_row = {
            "csv_name": "bad\x00value",
            "csv_age": "30",
        }
        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
        }

        with pytest.raises(
            TracecatImportError,
            match="Invalid null byte in column 'name' at CSV row 2",
        ):
            csv_importer.map_row(csv_row, column_mapping, row_number=2)

    def test_map_row_ignores_none_restkey(self, csv_importer: CSVImporter) -> None:
        """Test malformed rows with DictReader restkey entries do not crash mapping."""
        raw_row: dict[str | None, str | list[str]] = {
            "csv_name": "John Doe",
            "csv_age": "30",
            None: ["extra", "values"],
        }
        csv_row = cast(Mapping[str, str | None], raw_row)
        column_mapping = {
            "csv_name": "name",
            "csv_age": "age",
        }

        mapped_row = csv_importer.map_row(csv_row, column_mapping, row_number=2)

        assert mapped_row == {
            "name": "John Doe",
            "age": 30,
        }

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
        mock_service.batch_insert_rows.assert_called_once_with(
            mock_table, chunk, chunk_size=csv_importer.chunk_size
        )
        assert csv_importer.total_rows_inserted == 2

    @pytest.mark.anyio
    async def test_process_empty_chunk(self, csv_importer: CSVImporter) -> None:
        """Test processing an empty chunk."""
        mock_service = AsyncMock(spec=TablesService)
        mock_table = Mock(spec=Table)

        await csv_importer.process_chunk([], mock_service, mock_table)

        mock_service.batch_insert_rows.assert_not_called()
        assert csv_importer.total_rows_inserted == 0


class TestCSVSchemaInferer:
    """Tests for CSV schema inference utilities."""

    def test_infers_types_and_names(self) -> None:
        """Ensure types and sanitised names are derived correctly."""
        headers = [
            "Name",
            "Age",
            "Active",
            "Score",
            "Joined",
            "Identifier",
            "Metadata",
        ]
        uuid_value = str(uuid4())
        rows = [
            {
                "Name": "Alice",
                "Age": "30",
                "Active": "true",
                "Score": "88.5",
                "Joined": "2024-01-01T12:30:00Z",
                "Identifier": uuid_value,
                "Metadata": '{"team": "alpha"}',
            },
            {
                "Name": "Bob",
                "Age": "41",
                "Active": "false",
                "Score": "70",
                "Joined": "2024-02-11",
                "Identifier": uuid_value,
                "Metadata": '{"team": "beta"}',
            },
        ]

        inferer = CSVSchemaInferer.initialise(headers)
        for row in rows:
            inferer.observe(row)
        columns = inferer.result()

        assert [column.name for column in columns] == [
            "name",
            "age",
            "active",
            "score",
            "joined",
            "identifier",
            "metadata",
        ]
        type_map = {column.name: column.type for column in columns}
        assert type_map["name"] is SqlType.TEXT
        assert type_map["age"] is SqlType.INTEGER
        assert type_map["active"] is SqlType.BOOLEAN
        assert type_map["score"] is SqlType.NUMERIC
        assert type_map["joined"] is SqlType.TIMESTAMPTZ
        assert type_map["identifier"] is SqlType.UUID
        assert type_map["metadata"] is SqlType.JSONB

    def test_handles_duplicate_and_invalid_headers(self) -> None:
        """Ensure duplicate and invalid headers are auto-fixed."""
        headers = ["First Name", "123count", ""]
        rows = [
            {
                "First Name": "Alice",
                "123count": "5",
                "": "",
            }
        ]

        inferer = CSVSchemaInferer.initialise(headers)
        for row in rows:
            inferer.observe(row)
        columns = inferer.result()

        assert [column.name for column in columns] == [
            "firstname",
            "col_2_123count",
            "col_3",
        ]
        mapping = inferer.column_mapping
        assert mapping["First Name"] == "firstname"
        assert mapping["123count"] == "col_2_123count"
        assert mapping[""] == "col_3"

    def test_rejects_duplicate_headers(self) -> None:
        """Duplicate CSV headers should raise an import error."""
        headers = ["Name", "Age", "Name"]
        with pytest.raises(TracecatImportError, match="Duplicate columns: Name"):
            CSVSchemaInferer.initialise(headers)

    def test_rejects_blank_duplicate_headers(self) -> None:
        headers = ["", " ", ""]
        with pytest.raises(TracecatImportError, match="Duplicate columns: <empty>"):
            CSVSchemaInferer.initialise(headers)
