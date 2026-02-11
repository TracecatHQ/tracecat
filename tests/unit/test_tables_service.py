from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.db.models import Table
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.logger import logger
from tracecat.pagination import CursorPaginationParams
from tracecat.tables.common import parse_postgres_default
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="function")
async def tables_service(session: AsyncSession, svc_admin_role: Role) -> TablesService:
    """Fixture to create a TablesService instance using an admin role."""
    return TablesService(session=session, role=svc_admin_role)


@pytest.fixture(scope="function")
async def svc_editor_role(svc_workspace) -> Role:  # type: ignore[override]
    """Workspace editor role for tables tests."""
    return Role(
        type="user",
        access_level=AccessLevel.BASIC,
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        workspace_role=WorkspaceRole.EDITOR,
        user_id=uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture(scope="function")
async def tables_service_editor(
    session: AsyncSession, svc_editor_role: Role
) -> TablesService:
    """TablesService bound to a workspace editor role."""
    return TablesService(session=session, role=svc_editor_role)


@pytest.fixture(scope="function")
async def tables_service_basic(session: AsyncSession, svc_role: Role) -> TablesService:
    """TablesService bound to a basic member without workspace role."""
    return TablesService(session=session, role=svc_role)


# New fixture to create a table with 'name' and 'age' columns for row tests
@pytest.fixture
async def table(tables_service: TablesService) -> Table:
    """Fixture to create a table and add two columns ('name' and 'age') for row tests."""
    # Now create_table handles columns directly
    table = await tables_service.create_table(
        TableCreate(
            name="row_table",
            columns=[
                TableColumnCreate(
                    name="name", type=SqlType.TEXT, nullable=True, default=None
                ),
                TableColumnCreate(
                    name="age", type=SqlType.INTEGER, nullable=True, default=None
                ),
            ],
        )
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

    async def test_editor_can_create_and_delete_table(
        self, tables_service_editor: TablesService
    ) -> None:
        """Workspace editors should be allowed to mutate tables."""

        table = await tables_service_editor.create_table(
            TableCreate(name="editor_table")
        )
        fetched = await tables_service_editor.get_table(table.id)
        assert fetched.name == "editor_table"

        await tables_service_editor.delete_table(table)
        with pytest.raises(TracecatNotFoundError):
            await tables_service_editor.get_table(table.id)

    async def test_basic_member_cannot_create_table(
        self, tables_service_basic: TablesService
    ) -> None:
        """Members lacking a workspace role should be rejected for DDL."""

        with pytest.raises(TracecatAuthorizationError):
            await tables_service_basic.create_table(TableCreate(name="blocked_table"))

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

    async def test_create_table_with_columns(
        self, tables_service: TablesService
    ) -> None:
        """Test that create_table actually creates columns when specified.

        This test ensures the bug is fixed where create_table was not
        creating columns despite them being in the TableCreate params.
        """
        # Create a table with columns
        table_create = TableCreate(
            name="test_table_with_cols",
            columns=[
                TableColumnCreate(
                    name="username",
                    type=SqlType.TEXT,
                    nullable=False,
                ),
                TableColumnCreate(
                    name="email",
                    type=SqlType.TEXT,
                    nullable=True,
                ),
                TableColumnCreate(
                    name="score",
                    type=SqlType.INTEGER,
                    nullable=True,
                    default=0,
                ),
            ],
        )
        created_table = await tables_service.create_table(table_create)

        # Retrieve the table with columns
        retrieved_table = await tables_service.get_table(created_table.id)

        # Verify all columns were created
        assert len(retrieved_table.columns) == 3

        # Check column names
        column_names = {col.name for col in retrieved_table.columns}
        assert "username" in column_names
        assert "email" in column_names
        assert "score" in column_names

        # Verify column properties
        for col in retrieved_table.columns:
            if col.name == "username":
                assert col.type == SqlType.TEXT.value
                assert col.nullable is False
            elif col.name == "email":
                assert col.type == SqlType.TEXT.value
                assert col.nullable is True
            elif col.name == "score":
                assert col.type == SqlType.INTEGER.value
                assert col.nullable is True
                assert col.default == "0"  # Default values are stored as strings

    async def test_import_table_from_csv(self, tables_service: TablesService) -> None:
        """Importing a CSV should create table, columns, and rows."""
        csv_content = "\n".join(
            [
                "Full Name,Age,Active,Joined",
                "Alice,30,true,2024-01-01T12:00:00Z",
                "Bob,25,false,2024-01-02",
            ]
        )

        (
            table,
            rows_inserted,
            inferred_columns,
        ) = await tables_service.import_table_from_csv(
            contents=csv_content.encode(),
            filename="People.csv",
        )

        assert table.name == "people"
        assert rows_inserted == 2
        mapping = {col.original_name: col.name for col in inferred_columns}
        assert mapping["Full Name"] == "fullname"
        assert mapping["Age"] == "age"
        assert mapping["Active"] == "active"

        retrieved_table = await tables_service.get_table(table.id)
        rows = await tables_service.list_rows(retrieved_table)
        assert len(rows) == 2
        rows_by_name = {row["fullname"]: row for row in rows}
        assert rows_by_name.keys() == {"Alice", "Bob"}
        assert isinstance(rows_by_name["Alice"]["age"], int)
        assert isinstance(rows_by_name["Alice"]["active"], bool)
        assert isinstance(rows_by_name["Bob"]["age"], int)
        assert isinstance(rows_by_name["Bob"]["active"], bool)

        second_table, _, _ = await tables_service.import_table_from_csv(
            contents=csv_content.encode(),
            filename="People.csv",
        )
        assert second_table.name == "people_1"

    async def test_import_table_from_csv_json_arrays(
        self, tables_service: TablesService
    ) -> None:
        """JSON array cells should be stored as JSONB instead of plain text."""
        csv_content = "\n".join(
            [
                "Name,Tags",
                'Widget,"[""foo"", ""bar""]"',
            ]
        )

        table, inserted, inferred_columns = await tables_service.import_table_from_csv(
            contents=csv_content.encode(),
            filename="JsonArrays.csv",
        )

        assert inserted == 1
        tags_column = next(
            column for column in inferred_columns if column.original_name == "Tags"
        )
        assert tags_column.type is SqlType.JSONB

        retrieved_table = await tables_service.get_table(table.id)
        rows = await tables_service.list_rows(retrieved_table)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "Widget"
        assert row["tags"] == ["foo", "bar"]

    async def test_import_table_handles_empty_numeric_values(
        self, tables_service: TablesService
    ) -> None:
        """Empty cells in numeric columns should be treated as NULL."""
        csv_content = "\n".join(
            [
                "Name,Age",
                "Alice,",
                "Bob,42",
            ]
        )

        table, _, _ = await tables_service.import_table_from_csv(
            contents=csv_content.encode(),
            filename="Ages.csv",
        )

        retrieved_table = await tables_service.get_table(table.id)
        rows = await tables_service.list_rows(retrieved_table)
        assert len(rows) == 2
        rows_by_name = {row["name"]: row for row in rows}
        assert rows_by_name["Alice"]["age"] is None
        assert rows_by_name["Bob"]["age"] == 42

    async def test_import_table_from_csv_honors_chunk_size(
        self, tables_service: TablesService
    ) -> None:
        """Imports should allow larger chunk sizes without hitting the default cap."""

        header = "name"
        total_rows = 1500
        rows = [f"row_{i}" for i in range(total_rows)]
        csv_content = "\n".join([header, *rows])

        table, inserted, _ = await tables_service.import_table_from_csv(
            contents=csv_content.encode(),
            filename="Large.csv",
            chunk_size=total_rows,
        )

        assert inserted == total_rows

        retrieved_table = await tables_service.get_table(table.id)
        actual_rows = await tables_service.list_rows(retrieved_table, limit=total_rows)
        assert len(actual_rows) == total_rows

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

    async def test_create_column_rejects_plain_timestamp(
        self, tables_service: TablesService
    ) -> None:
        """Ensure TIMESTAMP is not allowed for user-defined columns."""
        table = await tables_service.create_table(TableCreate(name="reject_ts"))

        with pytest.raises(ValueError, match="Invalid type: TIMESTAMP"):
            await tables_service.create_column(
                table,
                TableColumnCreate(
                    name="legacy_ts",
                    type=SqlType.TIMESTAMP,
                ),
            )


class TestParsePostgresDefault:
    @pytest.fixture
    def parse_default(self):
        return parse_postgres_default

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            ("'attack'::text", "attack"),
            ("0::integer", "0"),
            ("true::boolean", "true"),
            ("'2024-01-01'::timestamp", "2024-01-01"),
            ("'2024-01-01 00:00:00+00'::timestamptz", "2024-01-01 00:00:00+00"),
            ("'foo'::pg_catalog.text", "foo"),
            ("'bar'::character varying", "bar"),
            ("'X'::text[]", "X"),
            ("'keep::inside'::text", "keep::inside"),
            ("'double'::text::text", "double"),
            ("'endswith::'::text", "endswith::"),
            ("'yes'", "yes"),
            ("42", "42"),
            # Should not strip inner casts when not at end
            ("nextval('seq'::regclass)", "nextval('seq'::regclass)"),
            # Should strip only a trailing cast on the whole expression
            ("nextval('seq'::regclass)::text", "nextval('seq'::regclass)"),
            # Trailing whitespace after cast should still be removed
            ("'abc'::text   ", "abc"),
        ],
    )
    def test_parse_postgres_default_variants(
        self, parse_default, raw: str | None, expected: str | None
    ) -> None:
        assert parse_default(raw) == expected


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

    async def test_create_single_column_unique_index(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test creating a unique index on a single column."""
        # Create the unique index on the 'name' column
        await tables_service.create_unique_index(table, "name")

        # Insert a row with a unique name
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "UniqueUser", "age": 25})
        )

        # Attempt to insert a row with the same name, should fail with integrity error
        with pytest.raises(ValueError) as exc_info:
            await tables_service.insert_row(
                table, TableRowInsert(data={"name": "UniqueUser", "age": 30})
            )

        # Verify the error message indicates a unique constraint violation
        error_msg = str(exc_info.value)
        assert "unique" in error_msg.lower() or "duplicate" in error_msg.lower()

    async def test_create_unique_index_with_existing_duplicates(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that creating a unique index fails if duplicates already exist."""
        # Insert rows with duplicate names
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "DuplicateUser", "age": 25})
        )
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "DuplicateUser", "age": 30})
        )

        # Attempt to create a unique index on 'name' should fail
        with pytest.raises(DBAPIError) as exc_info:
            await tables_service.create_unique_index(table, "name")

        # Verify the error indicates a duplicate key value issue
        error_msg = str(exc_info.value)
        assert "duplicate" in error_msg.lower() or "already exists" in error_msg.lower()

    async def test_create_multiple_unique_index_fails(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that creating multiple unique indexes fails."""
        await tables_service.create_unique_index(table, "name")
        with pytest.raises(ValueError) as exc_info:
            await tables_service.create_unique_index(table, "age")

        assert "Table cannot have multiple unique indexes" in str(exc_info.value)


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

    async def test_upsert_single_column_unique_index(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test upserting with a single column unique index."""
        # Insert initial row
        row_insert = TableRowInsert(data={"name": "John", "age": 30})
        inserted = await tables_service.insert_row(table, row_insert)
        assert inserted is not None, "Inserted row should not be None"

        # Create unique index on name column
        await tables_service.create_unique_index(table, "name")

        # Upsert using name as unique index
        upsert_data = {"name": "John", "age": 35}
        upsert_insert = TableRowInsert(data=upsert_data, upsert=True)
        upserted = await tables_service.insert_row(table, upsert_insert)

        # Verify row was updated
        assert upserted["name"] == "John"
        assert upserted["age"] == 35

        # Verify only one row exists
        rows = await tables_service.list_rows(table)
        assert len(rows) == 1, "Only one row should exist after upsert"

        assert "updated_at" in upserted
        assert isinstance(upserted["updated_at"], datetime)

    async def test_upsert_index_required(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that upsert fails without a unique index."""
        # Insert a row
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "TestUser", "age": 40})
        )

        # Attempt upsert without index
        with pytest.raises(ValueError) as exc_info:
            upsert_insert = TableRowInsert(
                data={"name": "TestUser", "age": 41}, upsert=True
            )
            await tables_service.insert_row(table, upsert_insert)

        # Verify error message
        assert "Table must have at least one unique index for upsert" in str(
            exc_info.value
        )

    async def test_upsert_index_field_required(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that upsert fails if the field is not in the index."""
        await tables_service.create_unique_index(table, "name")

        with pytest.raises(ValueError) as exc_info:
            upsert_insert = TableRowInsert(data={"age": 41}, upsert=True)
            await tables_service.insert_row(table, upsert_insert)

        assert "Data to upsert must contain the unique index column" in str(
            exc_info.value
        )

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

    async def test_batch_insert_rows_with_upsert(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test batch inserting with upsert functionality."""
        # Create unique index on name column first
        await tables_service.create_unique_index(table, "name")

        # Initial batch insert
        initial_rows = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
            {"name": "Carol", "age": 35},
        ]
        inserted_count = await tables_service.batch_insert_rows(table, initial_rows)
        assert inserted_count == 3

        # Verify initial insert
        all_rows = await tables_service.list_rows(table)
        assert len(all_rows) == 3

        # Batch upsert with some existing and some new rows
        upsert_rows = [
            {"name": "Alice", "age": 26},  # Update existing
            {"name": "Bob", "age": 31},  # Update existing
            {"name": "David", "age": 40},  # Insert new
            {"name": "Eve", "age": 45},  # Insert new
        ]
        upserted_count = await tables_service.batch_insert_rows(
            table, upsert_rows, upsert=True
        )
        assert upserted_count == 4  # 2 updates + 2 inserts

        # Verify final state
        final_rows = await tables_service.list_rows(table)
        assert len(final_rows) == 5  # 3 original + 2 new

        # Check updated values
        name_to_age = {row["name"]: row["age"] for row in final_rows}
        assert name_to_age["Alice"] == 26  # Updated
        assert name_to_age["Bob"] == 31  # Updated
        assert name_to_age["Carol"] == 35  # Unchanged
        assert name_to_age["David"] == 40  # New
        assert name_to_age["Eve"] == 45  # New

    async def test_batch_upsert_no_null_overwrite(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that batch upsert doesn't overwrite columns with NULL when rows have different columns."""
        # Create unique index on name column
        await tables_service.create_unique_index(table, "name")

        # Insert initial row with both name and age
        initial_row = {"name": "Alice", "age": 25}
        await tables_service.insert_row(table, TableRowInsert(data=initial_row))

        # Verify initial state
        rows = await tables_service.list_rows(table)
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
        assert rows[0]["age"] == 25

        # Batch upsert with rows having different columns
        # First row has only name (missing age)
        # Second row has both name and age
        upsert_rows = [
            {"name": "Alice"},  # Missing age column - should NOT nullify existing age
            {"name": "Bob", "age": 30},  # New row with both columns
        ]

        await tables_service.batch_insert_rows(table, upsert_rows, upsert=True)

        # Verify that Alice's age was NOT overwritten with NULL
        final_rows = await tables_service.list_rows(table)
        assert len(final_rows) == 2

        alice_row = next(row for row in final_rows if row["name"] == "Alice")
        bob_row = next(row for row in final_rows if row["name"] == "Bob")

        # Alice's age should remain unchanged (not NULL)
        assert alice_row["age"] == 25
        # Bob should have the specified age
        assert bob_row["age"] == 30

    async def test_batch_upsert_without_index_fails(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that batch upsert fails when table has no unique index."""
        # Insert some initial data
        initial_rows = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
        ]
        await tables_service.batch_insert_rows(table, initial_rows)

        # Attempt batch upsert without unique index
        upsert_rows = [
            {"name": "Alice", "age": 26},
            {"name": "Carol", "age": 35},
        ]

        with pytest.raises(ValueError) as exc_info:
            await tables_service.batch_insert_rows(table, upsert_rows, upsert=True)

        assert "Table must have at least one unique index for upsert" in str(
            exc_info.value
        )

    async def test_batch_upsert_missing_index_column_fails(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test that batch upsert fails when rows don't contain the unique index column."""
        # Create unique index on name column
        await tables_service.create_unique_index(table, "name")

        # Attempt batch upsert with rows missing the index column
        upsert_rows = [
            {"age": 25},  # Missing 'name' column
            {"age": 30},  # Missing 'name' column
        ]

        with pytest.raises(ValueError) as exc_info:
            await tables_service.batch_insert_rows(table, upsert_rows, upsert=True)

        assert "Each row to upsert must contain the unique index column" in str(
            exc_info.value
        )

    async def test_batch_upsert_with_mixed_operations(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """Test batch upsert with a mix of inserts and updates in a single batch."""
        # Create unique index on name column
        await tables_service.create_unique_index(table, "name")

        # Insert initial data
        initial_rows = [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30},
        ]
        await tables_service.batch_insert_rows(table, initial_rows)

        # Batch upsert with mixed operations
        mixed_rows = [
            {"name": "Alice", "age": 26},  # Update
            {"name": "Carol", "age": 35},  # Insert
            {"name": "Bob", "age": 31},  # Update
            {"name": "David", "age": 40},  # Insert
            {"name": "Eve", "age": 45},  # Insert
        ]

        count = await tables_service.batch_insert_rows(table, mixed_rows, upsert=True)
        assert count == 5  # 2 updates + 3 inserts

        # Verify final state
        final_rows = await tables_service.list_rows(table)
        assert len(final_rows) == 5

        # Check all values
        name_to_age = {row["name"]: row["age"] for row in final_rows}
        assert name_to_age == {
            "Alice": 26,
            "Bob": 31,
            "Carol": 35,
            "David": 40,
            "Eve": 45,
        }

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
            TableColumnCreate(name="int_col", type=SqlType.INTEGER),
            TableColumnCreate(name="numeric_col", type=SqlType.NUMERIC),
            TableColumnCreate(name="bool_col", type=SqlType.BOOLEAN),
            TableColumnCreate(name="json_col", type=SqlType.JSONB),
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
        naive_timestamp = datetime(2024, 2, 24, 12, 0)
        expected_timestamp = naive_timestamp.replace(tzinfo=UTC)
        test_json = {"key": "value", "nested": {"list": [1, 2, 3]}}

        # Create test data covering all types
        test_data = {
            "text_col": "Hello, World!",
            "int_col": 42,
            "numeric_col": Decimal("3.14159"),
            "bool_col": True,
            "json_col": test_json,
            "timestamptz_col": naive_timestamp,
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
        assert retrieved["int_col"] == 42
        assert retrieved["numeric_col"] == Decimal("3.14159")
        assert retrieved["bool_col"] is True
        assert retrieved["json_col"] == test_json

        # DateTime comparisons
        retrieved_timestamptz = retrieved["timestamptz_col"]
        assert isinstance(retrieved_timestamptz, datetime)
        assert retrieved_timestamptz == expected_timestamp
        assert retrieved_timestamptz.tzinfo == UTC

        # UUID comparison
        assert str(retrieved["uuid_col"]) == str(test_uuid)

    async def test_timestamptz_normalisation(
        self, tables_service: TablesService, complex_table: Table
    ) -> None:
        """Ensure TIMESTAMPTZ values are normalised to UTC on insert and update."""
        naive_value = datetime(2024, 3, 1, 10, 30)
        inserted = await tables_service.insert_row(
            complex_table, TableRowInsert(data={"timestamptz_col": naive_value})
        )

        expected_insert_value = naive_value.replace(tzinfo=UTC)
        assert inserted["timestamptz_col"] == expected_insert_value

        row_id = inserted["id"]
        offset_zone = timezone(timedelta(hours=-5))
        aware_value = datetime(2024, 3, 1, 5, 30, tzinfo=offset_zone)
        updated = await tables_service.update_row(
            complex_table, row_id, {"timestamptz_col": aware_value}
        )
        expected_update_value = aware_value.astimezone(UTC)
        assert updated["timestamptz_col"] == expected_update_value

        batch_rows = [
            {"timestamptz_col": datetime(2024, 3, 2, 9, 0)},
            {
                "timestamptz_col": datetime(
                    2024, 3, 2, 6, 0, tzinfo=timezone(timedelta(hours=-3))
                )
            },
        ]
        affected = await tables_service.batch_insert_rows(complex_table, batch_rows)
        assert affected == 2

        rows = await tables_service.list_rows(complex_table)
        # Extract non-null TIMESTAMPTZ values for verification
        extracted = [row["timestamptz_col"] for row in rows if row["timestamptz_col"]]
        assert len(extracted) == 3
        assert all(value.tzinfo == UTC for value in extracted)

        expected_values = sorted(
            [
                expected_update_value,
                datetime(2024, 3, 2, 9, 0, tzinfo=UTC),
                datetime(2024, 3, 2, 9, 0, tzinfo=UTC),
            ]
        )
        assert sorted(extracted) == expected_values

    async def test_date_coercion_on_insert_and_update(
        self, tables_service: TablesService
    ) -> None:
        """Ensure DATE values are coerced before binding to the database."""
        table = await tables_service.create_table(TableCreate(name="date_type_table"))
        await tables_service.create_column(
            table, TableColumnCreate(name="date_col", type=SqlType.DATE)
        )

        inserted = await tables_service.insert_row(
            table, TableRowInsert(data={"date_col": "2026-02-06"})
        )
        assert inserted["date_col"] == date(2026, 2, 6)

        updated = await tables_service.update_row(
            table,
            inserted["id"],
            {"date_col": datetime(2026, 2, 7, 12, 30)},
        )
        assert updated["date_col"] == date(2026, 2, 7)

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
                {"timestamptz_col": "not-a-timestamp"},
                "Invalid ISO datetime string: 'not-a-timestamp'",
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
            "int_col": 0,  # Zero
            "numeric_col": Decimal("0.0"),  # Zero decimal
            "bool_col": False,  # False boolean
            "json_col": {},  # Empty JSON
            "timestamptz_col": datetime(
                2025, 3, 15, 12, 0, 0, 0, tzinfo=UTC
            ),  # Arbitrary future datetime
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
        assert retrieved["int_col"] == 0
        assert retrieved["numeric_col"] == Decimal("0.0")
        assert retrieved["bool_col"] is False
        assert retrieved["json_col"] == {}

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

    async def test_select_type(self, tables_service: TablesService) -> None:
        """Test SELECT column type with options validation."""
        table = await tables_service.create_table(TableCreate(name="select_test"))
        await tables_service.create_column(
            table,
            TableColumnCreate(
                name="status",
                type=SqlType.SELECT,
                options=["open", "closed", "pending"],
            ),
        )
        await tables_service.session.refresh(table)

        # Insert a valid option
        row = await tables_service.insert_row(
            table, TableRowInsert(data={"status": "open"})
        )
        assert row is not None
        retrieved = await tables_service.get_row(table, row["id"])
        assert retrieved["status"] == "open"

    async def test_multi_select_type(self, tables_service: TablesService) -> None:
        """Test MULTI_SELECT column type with list values."""
        table = await tables_service.create_table(TableCreate(name="multiselect_test"))
        await tables_service.create_column(
            table,
            TableColumnCreate(
                name="tags",
                type=SqlType.MULTI_SELECT,
                options=["bug", "feature", "docs", "urgent"],
            ),
        )
        await tables_service.session.refresh(table)

        # Insert multiple selections
        row = await tables_service.insert_row(
            table, TableRowInsert(data={"tags": ["bug", "urgent"]})
        )
        assert row is not None
        retrieved = await tables_service.get_row(table, row["id"])
        assert set(retrieved["tags"]) == {"bug", "urgent"}


@pytest.mark.anyio
class TestTableSearch:
    """Test search_rows functionality."""

    @pytest.fixture
    async def search_table(self, tables_service: TablesService) -> Table:
        """Create a table with text data for search testing."""
        table = await tables_service.create_table(
            TableCreate(
                name="search_test",
                columns=[
                    TableColumnCreate(name="title", type=SqlType.TEXT),
                    TableColumnCreate(name="body", type=SqlType.TEXT),
                    TableColumnCreate(name="count", type=SqlType.INTEGER),
                ],
            )
        )
        # Insert test data
        rows = [
            {
                "title": "Security Alert",
                "body": "Suspicious login detected",
                "count": 1,
            },
            {
                "title": "Network Issue",
                "body": "Connection timeout occurred",
                "count": 2,
            },
            {"title": "Security Patch", "body": "Updated firewall rules", "count": 3},
            {"title": "Normal Activity", "body": "All systems operational", "count": 4},
        ]
        for row_data in rows:
            await tables_service.insert_row(table, TableRowInsert(data=row_data))
        return table

    async def test_search_rows_text(
        self, tables_service: TablesService, search_table: Table
    ) -> None:
        """Full-text search across text columns."""
        results = await tables_service.search_rows(search_table, search_term="Security")
        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert "Security Alert" in titles
        assert "Security Patch" in titles

    async def test_search_rows_no_match(
        self, tables_service: TablesService, search_table: Table
    ) -> None:
        """Search with no matching term should return empty."""
        results = await tables_service.search_rows(
            search_table, search_term="nonexistent_xyz"
        )
        assert results == []

    async def test_search_rows_with_date_filter(
        self, tables_service: TablesService, search_table: Table
    ) -> None:
        """Date range filtering on search results."""
        now = datetime.now(tz=UTC)
        # All rows were just inserted, so filtering with past start_time should return all
        results = await tables_service.search_rows(
            search_table,
            start_time=now - timedelta(minutes=5),
            end_time=now + timedelta(minutes=5),
        )
        assert len(results) == 4

        # Future start_time should return none
        results = await tables_service.search_rows(
            search_table,
            start_time=now + timedelta(hours=1),
        )
        assert results == []

    async def test_search_rows_null_bytes_rejected(
        self, tables_service: TablesService, search_table: Table
    ) -> None:
        """Null bytes in search term should be rejected."""
        with pytest.raises(ValueError):
            await tables_service.search_rows(
                search_table, search_term="test\x00injection"
            )

    async def test_search_rows_with_limit(
        self, tables_service: TablesService, search_table: Table
    ) -> None:
        """Search with limit parameter."""
        results = await tables_service.search_rows(search_table, limit=2)
        assert len(results) == 2


@pytest.mark.anyio
class TestTablePagination:
    """Test cursor-based pagination for list_rows_paginated."""

    @pytest.fixture
    async def paginated_table(self, tables_service: TablesService) -> Table:
        """Create a table with enough rows for pagination testing."""
        table = await tables_service.create_table(
            TableCreate(
                name="pagination_test",
                columns=[
                    TableColumnCreate(name="label", type=SqlType.TEXT),
                    TableColumnCreate(name="seq", type=SqlType.INTEGER),
                ],
            )
        )
        for i in range(10):
            await tables_service.insert_row(
                table, TableRowInsert(data={"label": f"row_{i:02d}", "seq": i})
            )
        return table

    async def test_list_rows_paginated_forward(
        self, tables_service: TablesService, paginated_table: Table
    ) -> None:
        """Forward cursor pagination should return pages in order."""
        # First page
        params = CursorPaginationParams(limit=3)
        page1 = await tables_service.list_rows_paginated(paginated_table, params)
        assert len(page1.items) == 3
        assert page1.has_more is True
        assert page1.next_cursor is not None

        # Second page
        params2 = CursorPaginationParams(limit=3, cursor=page1.next_cursor)
        page2 = await tables_service.list_rows_paginated(paginated_table, params2)
        assert len(page2.items) == 3
        assert page2.has_previous is True

        # No overlap between pages
        page1_ids = {r["id"] for r in page1.items}
        page2_ids = {r["id"] for r in page2.items}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_list_rows_paginated_reverse(
        self, tables_service: TablesService, paginated_table: Table
    ) -> None:
        """Reverse pagination should navigate backward."""
        # Get first page
        params = CursorPaginationParams(limit=3)
        page1 = await tables_service.list_rows_paginated(paginated_table, params)

        # Get second page
        params2 = CursorPaginationParams(limit=3, cursor=page1.next_cursor)
        page2 = await tables_service.list_rows_paginated(paginated_table, params2)

        # Go back to first page using prev_cursor
        assert page2.prev_cursor is not None
        params_prev = CursorPaginationParams(
            limit=3, cursor=page2.prev_cursor, reverse=True
        )
        page_back = await tables_service.list_rows_paginated(
            paginated_table, params_prev
        )
        assert len(page_back.items) == 3

    async def test_list_rows_paginated_with_sort_column(
        self, tables_service: TablesService, paginated_table: Table
    ) -> None:
        """Pagination with a custom sort column."""
        params = CursorPaginationParams(limit=5)
        result = await tables_service.list_rows_paginated(
            paginated_table, params, order_by="seq", sort="asc"
        )
        assert len(result.items) == 5
        # Should be sorted ascending by seq
        seqs = [r["seq"] for r in result.items]
        assert seqs == sorted(seqs)

    async def test_list_rows_paginated_full_traverse(
        self, tables_service: TablesService, paginated_table: Table
    ) -> None:
        """Traversing all pages should yield all rows."""
        all_rows: list[dict] = []
        cursor = None
        while True:
            params = CursorPaginationParams(limit=4, cursor=cursor)
            page = await tables_service.list_rows_paginated(paginated_table, params)
            all_rows.extend(page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert len(all_rows) == 10


@pytest.mark.anyio
class TestTableExistsRows:
    """Test exists_rows efficient EXISTS query."""

    async def test_exists_rows_found(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """exists_rows should return True when matching row exists."""
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Alice", "age": 30})
        )
        result = await tables_service.exists_rows(
            table.name, columns=["name"], values=["Alice"]
        )
        assert result is True

    async def test_exists_rows_not_found(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """exists_rows should return False when no matching row exists."""
        result = await tables_service.exists_rows(
            table.name, columns=["name"], values=["NonexistentPerson"]
        )
        assert result is False

    async def test_exists_rows_multiple_columns(
        self, tables_service: TablesService, table: Table
    ) -> None:
        """exists_rows with multiple column matching."""
        await tables_service.insert_row(
            table, TableRowInsert(data={"name": "Bob", "age": 25})
        )
        # Match on both columns
        assert (
            await tables_service.exists_rows(
                table.name, columns=["name", "age"], values=["Bob", 25]
            )
            is True
        )
        # Mismatch on age
        assert (
            await tables_service.exists_rows(
                table.name, columns=["name", "age"], values=["Bob", 99]
            )
            is False
        )
