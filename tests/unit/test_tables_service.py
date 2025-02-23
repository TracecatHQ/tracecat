from datetime import datetime
from uuid import UUID

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Table, TableColumn
from tracecat.tables.enums import SqlType
from tracecat.tables.models import (
    TableColumnCreate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.tables.service import TablesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def tables_service(session: AsyncSession, svc_admin_role: Role) -> TablesService:
    """Fixture to create a TablesService instance using an admin role."""
    return TablesService(session=session, role=svc_admin_role)


# New fixture to create a table with 'name' and 'age' columns for row tests
@pytest.fixture
async def table_with_columns(
    tables_service: TablesService,
) -> tuple[Table, TableColumn, TableColumn]:
    """Fixture to create a table and add two columns ('name' and 'age') for row tests."""
    table = await tables_service.create_table(TableCreate(name="row_table"))
    name_col = await tables_service.create_column(
        table,
        TableColumnCreate(name="name", type=SqlType.TEXT, nullable=True, default=None),
    )
    age_col = await tables_service.create_column(
        table,
        TableColumnCreate(
            name="age", type=SqlType.INTEGER, nullable=True, default=None
        ),
    )
    return table, name_col, age_col


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


@pytest.mark.anyio
class TestTableRows:
    async def test_insert_and_get_row(
        self, tables_service: TablesService, table_with_columns: tuple
    ) -> None:
        """Test inserting a new row and then retrieving it."""
        table, _, _ = table_with_columns

        # Insert a row using TableRowInsert
        row_insert = TableRowInsert(data={"name": "John", "age": 30})
        inserted = await tables_service.insert_row(table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        # Extract row_id from the returned object whether it's a dict or an object
        row_id: UUID = inserted["id"]

        # Retrieve the row using get_row
        retrieved = await tables_service.get_row(table, row_id)

        # Verify the inserted data
        assert retrieved["name"] == "John"
        assert retrieved["age"] == 30
        assert "created_at" in retrieved
        assert "updated_at" in retrieved

    async def test_update_row(
        self, tables_service: TablesService, table_with_columns: tuple
    ) -> None:
        """Test updating an existing row and verifying the data changed."""
        table, _, _ = table_with_columns

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
        self, tables_service: TablesService, table_with_columns: tuple
    ) -> None:
        """Test deleting a row and verifying it no longer exists."""
        table, _, _ = table_with_columns

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
        self, tables_service: TablesService, table_with_columns: tuple
    ) -> None:
        """Test the lookup_row method to filter rows by column values."""
        table, _, _ = table_with_columns

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
        results = await tables_service.lookup_row(
            table_name=table.name, columns=["name", "age"], values=["Bob", 40]
        )

        # Verify that only one row matches
        assert len(results) == 1
        result = results[0]
        assert result["name"] == "Bob"
        assert result["age"] == 40
