from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError, StatementError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Table
from tracecat.logger import logger
from tracecat.tables.enums import SqlType
from tracecat.tables.models import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.tables.service import TablesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="function")
async def tables_service(session: AsyncSession, svc_admin_role: Role) -> TablesService:
    """Fixture to create a TablesService instance using an admin role."""
    return TablesService(session=session, role=svc_admin_role)


# New fixture to create a table with 'name' and 'age' columns for row tests
@pytest.fixture
async def table(tables_service: TablesService) -> Table:
    """Fixture to create a table and add two columns ('name' and 'age') for row tests."""
    table = await tables_service.create_table(TableCreate(name="row_table"))
    await tables_service.create_column(
        table,
        TableColumnCreate(name="name", type=SqlType.TEXT, nullable=True, default=None),
    )
    await tables_service.create_column(
        table,
        TableColumnCreate(
            name="age", type=SqlType.INTEGER, nullable=True, default=None
        ),
    )
    return table


@pytest.mark.anyio
class TestTablesService:
    async def test_create_and_get_table(self, tables_service: TablesService) -> None:
        """Test creating a table and retrieving it by name and id."""
        # Create a table using TableCreate
        table_create = TableCreate(name="test_table")
        created_table = await tables_service.create_table(table_create)

        # Retrieve by name
        retrieved_table = await tables_service.get_table_by_name("test_table")
        assert retrieved_table.id == created_table.id
        assert retrieved_table.name == created_table.name

        # Retrieve by id
        retrieved_by_id = await tables_service.get_table(created_table.id)
        assert retrieved_by_id.id == created_table.id

    async def test_list_tables(self, tables_service: TablesService) -> None:
        """Test listing tables after creating multiple tables."""
        # Create two tables
        table1 = await tables_service.create_table(TableCreate(name="table_one"))
        table2 = await tables_service.create_table(TableCreate(name="table_two"))

        tables = await tables_service.list_tables()
        # Check that the created tables are in the list
        table_ids = {table.id for table in tables}
        assert table1.id in table_ids
        assert table2.id in table_ids

    async def test_update_table(self, tables_service: TablesService) -> None:
        """Test updating table metadata."""
        # Create table
        table = await tables_service.create_table(TableCreate(name="updatable_table"))

        # Update the table using TableUpdate
        update_params = TableUpdate(name="updated_table")
        updated_table = await tables_service.update_table(table, update_params)

        # Verify update
        assert updated_table.name == "updated_table"

        # Retrieve the updated table
        retrieved_table = await tables_service.get_table(updated_table.id)
        assert retrieved_table.name == "updated_table"

    async def test_delete_table(self, tables_service: TablesService) -> None:
        """Test deleting a table and expecting a not found error on retrieval."""
        # Create table
        table = await tables_service.create_table(TableCreate(name="deletable_table"))

        # Delete the table
        await tables_service.delete_table(table)

        # Attempt to retrieve the table; should raise TracecatNotFoundError
        with pytest.raises(TracecatNotFoundError):
            await tables_service.get_table_by_name("deletable_table")


@pytest.mark.anyio
class TestTableColumns:
    async def test_create_and_get_column(self, tables_service: TablesService) -> None:
        """Test adding a column to a table and retrieving it by its ID."""
        # Create a table first
        table = await tables_service.create_table(TableCreate(name="column_table"))

        # Create a new column using TableColumnCreate
        col_create = TableColumnCreate(
            name="age", type=SqlType.INTEGER, nullable=True, default=None
        )
        column = await tables_service.create_column(table, col_create)

        # Retrieve the column by id
        retrieved_col = await tables_service.get_column(table.id, column.id)
        assert retrieved_col.id == column.id
        assert retrieved_col.name == "age"
        assert retrieved_col.type == SqlType.INTEGER

    async def test_delete_column(self, tables_service: TablesService) -> None:
        """Test deleting a column from a table and ensuring it is removed."""
        # Create table and add a column
        table = await tables_service.create_table(
            TableCreate(name="delete_column_table")
        )
        col_create = TableColumnCreate(
            name="temp_column", type=SqlType.TEXT, nullable=True, default=None
        )
        column = await tables_service.create_column(table, col_create)

        # Delete the column
        await tables_service.delete_column(column)

        # Attempt to retrieve the deleted column; should raise TracecatNotFoundError
        with pytest.raises(TracecatNotFoundError):
            await tables_service.get_column(table.id, column.id)

    async def test_update_column(self, tables_service: TablesService) -> None:
        """Test updating a column's properties."""
        # Create a table first
        table = await tables_service.create_table(
            TableCreate(name="update_column_table")
        )

        # Create initial column
        col_create = TableColumnCreate(
            name="old_name", type=SqlType.TEXT, nullable=True
        )
        column = await tables_service.create_column(table, col_create)

        # Update the column with new properties
        col_update = TableColumnUpdate(
            name="new_name", nullable=False, default="default_value"
        )
        updated_column = await tables_service.update_column(column, col_update)

        # Verify the updates
        assert updated_column.name == "new_name"
        assert updated_column.nullable is False
        assert updated_column.default == "default_value"
        # Type should remain unchanged
        assert updated_column.type == SqlType.TEXT

        # Verify by retrieving the column again
        retrieved_column = await tables_service.get_column(table.id, column.id)
        assert retrieved_column.name == "new_name"
        assert retrieved_column.nullable is False
        assert retrieved_column.default == "default_value"
        assert retrieved_column.type == SqlType.TEXT


@pytest.mark.anyio
class TestTableRows:
    async def test_insert_and_get_row(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test inserting a new row and then retrieving it."""
        # Insert a row using TableRowInsert
        row_insert = TableRowInsert(data={"name": "John", "age": 30})
        inserted = await tables_service.insert_row(table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        # Extract row_id from the returned object whether it's a dict or an object
        row_id: UUID = inserted["id"]

        # Retrieve the row using get_row
        retrieved = await tables_service.get_row(table, row_id)

        # Verify the inserted data
        logger.info("Retrieved row", retrieved=retrieved)
        assert retrieved["name"] == "John"
        assert retrieved["age"] == 30
        assert "created_at" in retrieved
        assert "updated_at" in retrieved

    async def test_upsert_row(
        self, tables_service: TablesService, table: Table
    ) -> None:
        # First insert a row
        row_insert = TableRowInsert(data={"name": "John", "age": 30}, upsert=False)
        inserted = await tables_service.insert_row(table, row_insert)

        # Keep track of original values
        row_id = inserted["id"]
        original_created_at = inserted["created_at"]
        original_updated_at = inserted["updated_at"]

        # Attempt to upsert the same row with modified data
        upsert_data = {"name": "John Smith", "age": 31}
        upsert_insert = TableRowInsert(data={"id": row_id, **upsert_data}, upsert=True)
        upserted = await tables_service.insert_row(table, upsert_insert)

        # Verify the row was updated
        assert upserted["id"] == row_id, "ID should remain the same"
        assert upserted["name"] == "John Smith", "Name should be updated"
        assert upserted["age"] == 31, "Age should be updated"

        # Check if created_at remains the same but updated_at changed
        assert upserted["created_at"] == original_created_at, (
            "created_at should not change on upsert"
        )
        assert upserted["updated_at"] > original_updated_at, (
            "updated_at should be newer than original updated_at"
        )

        # Verify with a get operation
        retrieved = await tables_service.get_row(table, row_id)
        assert retrieved["name"] == "John Smith"
        assert retrieved["age"] == 31

        # Count rows to ensure no new row was created
        rows = await tables_service.list_rows(table)
        assert len(rows) == 1, "Only one row should exist after upsert"

        # Test upserting a new record (one that doesn't exist yet)
        new_upsert_data = {"name": "Emily", "age": 32}
        new_upsert = TableRowInsert(data=new_upsert_data, upsert=True)
        new_row = await tables_service.insert_row(table, new_upsert)

        # Verify the new row was created
        assert new_row["name"] == "Emily"
        assert new_row["age"] == 32

        # Count rows to ensure a new row was created
        rows = await tables_service.list_rows(table)
        assert len(rows) == 2, "Two rows should exist after upserting a new row"

    async def test_update_row(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test updating an existing row and verifying the data changed."""

        # Insert a row
        row_insert = TableRowInsert(data={"name": "Alice", "age": 25})
        inserted = await tables_service.insert_row(table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        row_id: UUID = inserted["id"]

        # Update the row: change age from 25 to 26
        updated = await tables_service.update_row(table, row_id, {"age": 26})
        assert updated is not None, "Updated row should not be None"

        # Verify the update
        assert updated["age"] == 26
        assert updated["name"] == "Alice"  # Name should remain unchanged
        assert "updated_at" in updated
        assert isinstance(updated["updated_at"], datetime)

    async def test_delete_row(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test deleting a row and verifying it no longer exists."""
        # Insert a row
        row_insert = TableRowInsert(data={"name": "Bob", "age": 40})
        inserted = await tables_service.insert_row(table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        row_id: UUID = inserted["id"]

        # Delete the row
        await tables_service.delete_row(table, row_id)

        # Attempt to retrieve the deleted row; should raise TracecatNotFoundError
        with pytest.raises(TracecatNotFoundError):
            await tables_service.get_row(table, row_id)

    async def test_lookup_row(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test the lookup_row method to filter rows by column values."""
        # Insert multiple rows
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Bob", "age": 40})
        )
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Carol", "age": 35})
        )
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Bob", "age": 45})
        )

        # Lookup rows where name is 'Bob' and age is 40
        results = await tables_service.lookup_rows(
            table_name=table.name, columns=["name", "age"], values=["Bob", 40]
        )

        # Verify that only one row matches
        assert len(results) == 1
        result = results[0]
        assert result["name"] == "Bob"
        assert result["age"] == 40

    async def test_list_rows(self, tables_service: TablesService, table: Table) -> None:
        """Test listing rows with pagination using limit and offset."""
        # Insert multiple test rows
        test_data = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
            {"name": "Carol", "age": 35},
            {"name": "David", "age": 40},
            {"name": "Eve", "age": 45},
        ]

        for data in test_data:
            await tables_service.insert_row(table, TableRowInsert(data=data))

        # Test default pagination (limit=100, offset=0)
        all_rows = await tables_service.list_rows(table)
        assert len(all_rows) == 5

        # Test with limit
        limited_rows = await tables_service.list_rows(table, limit=2)
        assert len(limited_rows) == 2
        assert limited_rows[0]["name"] == "Alice"
        assert limited_rows[1]["name"] == "Bob"

        # Test with offset
        offset_rows = await tables_service.list_rows(table, offset=2, limit=2)
        assert len(offset_rows) == 2
        assert offset_rows[0]["name"] == "Carol"
        assert offset_rows[1]["name"] == "David"

        # Test with offset that exceeds available rows
        empty_rows = await tables_service.list_rows(table, offset=10)
        assert len(empty_rows) == 0

    async def test_batch_insert_rows(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test batch inserting multiple rows."""

        # Test data
        rows = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
            {"name": "Carol", "age": 35},
        ]

        # Insert batch
        inserted_count = await tables_service.batch_insert_rows(table, rows)
        assert inserted_count == 3

        # Verify all rows were inserted
        all_rows = await tables_service.list_rows(table)
        assert len(all_rows) == 3
        names = {row["name"] for row in all_rows}
        assert names == {"Alice", "Bob", "Carol"}

    async def test_batch_insert_exceeding_chunk_size(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test batch insert fails when exceeding chunk size."""
        # Create more rows than the chunk size
        rows = [
            {"name": f"Person{i}", "age": i}
            for i in range(1001)  # Default chunk size is 1000
        ]

        # Should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            await tables_service.batch_insert_rows(table, rows)
        assert "exceeds maximum" in str(exc_info.value)

    async def test_batch_insert_rollback_on_error(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that no rows are inserted if there's an error with any row."""
        # Mix of valid and invalid data (invalid type for age)
        rows = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": "invalid"},  # This should cause an error
            {"name": "Carol", "age": 35},
        ]

        # Should raise DBAPIError
        with pytest.raises(DBAPIError):
            await tables_service.batch_insert_rows(table, rows)

        # Verify no rows were inserted (transaction rolled back)
        all_rows = await tables_service.list_rows(table)
        assert len(all_rows) == 0

    async def test_batch_insert_empty_list(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test batch insert with empty list."""
        # Insert empty list
        inserted_count = await tables_service.batch_insert_rows(table, [])
        assert inserted_count == 0

        # Verify no rows were inserted
        all_rows = await tables_service.list_rows(table)
        assert len(all_rows) == 0

    async def test_lookup_row_multiple(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test lookup_row returns multiple rows when more than one matching row exists."""
        # Insert two rows with the same data for lookup
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Charlie", "age": 50})
        )
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Charlie", "age": 60})
        )
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Charlie", "age": 70})
        )

        # Lookup rows where name is 'Charlie' and age is 50
        results = await tables_service.lookup_rows(
            table_name=table.name, columns=["name"], values=["Charlie"]
        )

        # Assert that we get more than one result
        assert len(results) == 3, "Expected multiple rows to be returned by lookup_row"
        assert results[0]["name"] == "Charlie"
        assert results[0]["age"] == 50
        assert results[1]["name"] == "Charlie"
        assert results[1]["age"] == 60
        assert results[2]["name"] == "Charlie"
        assert results[2]["age"] == 70

        # Now limit the results to 2
        results = await tables_service.lookup_rows(
            table_name=table.name, columns=["name"], values=["Charlie"], limit=2
        )
        assert len(results) == 2
        assert results[0]["name"] == "Charlie"
        assert results[0]["age"] == 50

        assert results[1]["name"] == "Charlie"
        assert results[1]["age"] == 60


@pytest.mark.anyio
class TestTableDataTypes:
    """Test suite for verifying all supported SQL data types."""

    @pytest.fixture
    async def complex_table(self, tables_service: TablesService) -> Table:
        """Create a table with columns for all supported SQL types."""
        table = await tables_service.create_table(TableCreate(name="type_test_table"))

        # Create columns for each SQL type
        columns = [
            TableColumnCreate(name="text_col", type=SqlType.TEXT),
            TableColumnCreate(name="varchar_col", type=SqlType.VARCHAR),
            TableColumnCreate(name="int_col", type=SqlType.INTEGER),
            TableColumnCreate(name="bigint_col", type=SqlType.BIGINT),
            TableColumnCreate(name="decimal_col", type=SqlType.DECIMAL),
            TableColumnCreate(name="bool_col", type=SqlType.BOOLEAN),
            TableColumnCreate(name="json_col", type=SqlType.JSONB),
            TableColumnCreate(name="timestamp_col", type=SqlType.TIMESTAMP),
            TableColumnCreate(name="timestamptz_col", type=SqlType.TIMESTAMPTZ),
            TableColumnCreate(name="uuid_col", type=SqlType.UUID),
        ]

        for column in columns:
            await tables_service.create_column(table, column)

        await tables_service.session.refresh(table)
        return table

    async def test_insert_all_types(
        self, tables_service: TablesService, complex_table: Table
    ) -> None:
        """Test inserting and retrieving values of all supported SQL types."""
        # Test data for each type
        test_uuid = uuid4()
        test_timestamp = datetime(2024, 2, 24, 12, 0)
        test_datetime_tz = datetime(2024, 2, 24, 12, 0, tzinfo=UTC)
        test_json = {"key": "value", "nested": {"list": [1, 2, 3]}}

        # Create test data covering all types
        test_data = {
            "text_col": "Hello, World!",
            "varchar_col": "Variable length text",
            "int_col": 42,
            "bigint_col": 9223372036854775807,  # max int64
            "decimal_col": Decimal("3.14159"),
            "bool_col": True,
            "json_col": test_json,
            "timestamp_col": test_timestamp,
            "timestamptz_col": test_datetime_tz,
            "uuid_col": test_uuid,
        }

        # Insert the test data
        row_insert = TableRowInsert(data=test_data)
        inserted = await tables_service.insert_row(complex_table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        # Retrieve the row
        row_id = inserted["id"]
        retrieved = await tables_service.get_row(complex_table, row_id)

        # Verify each column type and value
        assert retrieved["text_col"] == "Hello, World!"
        assert retrieved["varchar_col"] == "Variable length text"
        assert retrieved["int_col"] == 42
        assert retrieved["bigint_col"] == 9223372036854775807
        assert retrieved["decimal_col"] == Decimal("3.14159")
        assert retrieved["bool_col"] is True
        assert retrieved["json_col"] == test_json

        # DateTime comparisons
        retrieved_timestamp = retrieved["timestamp_col"]
        retrieved_timestamptz = retrieved["timestamptz_col"]
        assert isinstance(retrieved_timestamp, datetime)
        assert isinstance(retrieved_timestamptz, datetime)
        assert retrieved_timestamp == test_timestamp
        assert retrieved_timestamptz == test_datetime_tz

        # UUID comparison
        assert str(retrieved["uuid_col"]) == str(test_uuid)

    @pytest.mark.usefixtures("db")
    @pytest.mark.parametrize(
        "invalid_data,expected_error",
        [
            # Test invalid integer
            pytest.param(
                {"int_col": "not a number"},
                "('str' object cannot be interpreted as an integer)",
                id="invalid_integer",
            ),
            # Test invalid boolean
            pytest.param(
                {"bool_col": "not a boolean"},
                "Expected bool or 0/1, got str",
                id="invalid_boolean",
            ),
            # Test invalid UUID
            pytest.param(
                {"uuid_col": "not-a-uuid"},
                "invalid UUID",
                id="invalid_uuid",
            ),
            # Test invalid JSON - this raises TypeError directly
            pytest.param(
                {"json_col": object()},
                "Object of type object is not JSON serializable",
                id="invalid_json",
            ),
            # Test invalid timestamp
            pytest.param(
                {"timestamp_col": "not-a-timestamp"},
                "expected a datetime.date or datetime.datetime instance",
                id="invalid_timestamp",
            ),
        ],
        scope="function",
    )
    async def test_invalid_type_conversions(
        self,
        tables_service: TablesService,
        complex_table: Table,
        invalid_data: dict,
        expected_error: str,
    ) -> None:
        """Test that invalid type conversions are handled appropriately."""
        try:
            # Don't start a new transaction, just use the existing one
            with pytest.raises((DBAPIError, TypeError, StatementError)) as exc_info:
                row_insert = TableRowInsert(data=invalid_data)
                await tables_service.insert_row(complex_table, row_insert)

            # Log the actual error for debugging
            logger.info(
                "Got expected error",
                error_type=type(exc_info.value).__name__,
                error_msg=str(exc_info.value),
            )

            # Verify the error message
            assert expected_error in str(exc_info.value)

        except Exception as e:
            logger.error(
                "Unexpected error in test",
                error_type=type(e).__name__,
                error_msg=str(e),
                invalid_data=invalid_data,
            )
            # Rollback the existing transaction
            await tables_service.session.rollback()
            raise
        finally:
            # Always ensure we rollback after the test
            await tables_service.session.rollback()

    async def test_edge_cases(
        self, tables_service: TablesService, complex_table: Table
    ) -> None:
        """Test edge cases for each data type."""
        edge_cases = {
            "text_col": "",  # Empty string
            "varchar_col": "a" * 1000,  # Long string
            "int_col": 0,  # Zero
            "bigint_col": -9223372036854775808,  # min int64
            "decimal_col": Decimal("0.0"),  # Zero decimal
            "bool_col": False,  # False boolean
            "json_col": {},  # Empty JSON
            "timestamp_col": datetime(
                1, 1, 1, 0, 0
            ),  # Minimum datetime without timezone
            "timestamptz_col": datetime(
                2025, 3, 15, 12, 0, 0, 0, tzinfo=UTC
            ),  # Maximum datetime
            "uuid_col": UUID("00000000-0000-0000-0000-000000000000"),  # Nil UUID
        }

        # Insert edge cases
        row_insert = TableRowInsert(data=edge_cases)
        inserted = await tables_service.insert_row(complex_table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        # Retrieve and verify
        row_id = inserted["id"]
        retrieved = await tables_service.get_row(complex_table, row_id)

        # Verify each edge case
        assert retrieved["text_col"] == ""
        assert retrieved["varchar_col"] == "a" * 1000
        assert retrieved["int_col"] == 0
        assert retrieved["bigint_col"] == -9223372036854775808
        assert retrieved["decimal_col"] == Decimal("0.0")
        assert retrieved["bool_col"] is False
        assert retrieved["json_col"] == {}

        # DateTime comparisons - fix the timezone comparison issue
        assert (
            retrieved["timestamp_col"].replace(tzinfo=None)
            == edge_cases["timestamp_col"]
        )

        # For timestamptz, we need to handle the timezone comparison
        # The database might return a datetime with a different timezone representation
        # but equivalent time
        retrieved_timestamptz = retrieved["timestamptz_col"]
        expected_timestamptz = edge_cases["timestamptz_col"]

        # Compare the UTC timestamps instead of the datetime objects directly
        assert retrieved_timestamptz.astimezone(UTC).replace(
            tzinfo=None
        ) == expected_timestamptz.astimezone(UTC).replace(tzinfo=None)

        # UUID comparison using string representation
        assert str(retrieved["uuid_col"]) == str(edge_cases["uuid_col"])
