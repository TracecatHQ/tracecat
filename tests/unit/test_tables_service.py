from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Table
from tracecat.exceptions import TracecatNotFoundError
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
    """TablesService bound to an admin role for workspace operations."""
    return TablesService(session=session, role=svc_admin_role)


@pytest.fixture
async def people_table(tables_service: TablesService) -> Table:
    """Baseline table with simple TEXT/INTEGER columns."""
    created = await tables_service.create_table(
        TableCreate(
            name="people",
            columns=[
                TableColumnCreate(name="name", type=SqlType.TEXT, nullable=False),
                TableColumnCreate(name="age", type=SqlType.INTEGER, nullable=True),
            ],
        )
    )
    return await tables_service.get_table(created.id)


@pytest.mark.anyio
class TestTableLifecycle:
    async def test_create_table_with_columns_persists_metadata(
        self, tables_service: TablesService
    ) -> None:
        table = await tables_service.create_table(
            TableCreate(
                name="customers",
                columns=[
                    TableColumnCreate(name="email", type=SqlType.TEXT, nullable=False),
                    TableColumnCreate(
                        name="loyalty_score",
                        type=SqlType.INTEGER,
                        nullable=True,
                        default=0,
                    ),
                ],
            )
        )
        stored = await tables_service.get_table(table.id)

        column_names = {column.name for column in stored.columns}
        assert column_names == {"email", "loyalty_score"}

        loyalty = next(col for col in stored.columns if col.name == "loyalty_score")
        assert loyalty.type == SqlType.INTEGER.value
        assert loyalty.default == "0"
        
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

    async def test_update_table_changes_name(
        self, tables_service: TablesService
    ) -> None:
        table = await tables_service.create_table(TableCreate(name="staging"))
        updated = await tables_service.update_table(table, TableUpdate(name="live"))

        assert updated.name == "live"
        reloaded = await tables_service.get_table(updated.id)
        assert reloaded.name == "live"

    async def test_delete_table_removes_metadata(
        self, tables_service: TablesService
    ) -> None:
        table = await tables_service.create_table(TableCreate(name="temporary"))
        await tables_service.delete_table(table)

        with pytest.raises(TracecatNotFoundError):
            await tables_service.get_table_by_name("temporary")


@pytest.mark.anyio
class TestColumnManagement:
    async def test_create_column_rejects_disallowed_type(
        self, tables_service: TablesService
    ) -> None:
        table = await tables_service.create_table(TableCreate(name="reject_ts"))

        with pytest.raises(ValueError, match="Invalid type: SqlType.TIMESTAMP"):
            await tables_service.create_column(
                table,
                TableColumnCreate(name="legacy_ts", type=SqlType.TIMESTAMP),
            )

    async def test_update_column_sanitises_and_migrates_rows(
        self, tables_service: TablesService, people_table: Table
    ) -> None:
        await tables_service.insert_row(
            people_table,
            TableRowInsert(data={"name": "Alice", "age": 33}),
        )
        original_column = people_table.columns[0]

        await tables_service.update_column(
            original_column, TableColumnUpdate(name="Full Name!!")
        )

        refreshed = await tables_service.get_table(people_table.id)
        renamed = next(col for col in refreshed.columns if col.id == original_column.id)
        assert renamed.name == "fullname"

        rows = await tables_service.list_rows(refreshed)
        assert rows[0]["fullname"] == "Alice"
        assert "name" not in rows[0]

    async def test_create_unique_index_allows_only_one(
        self, tables_service: TablesService
    ) -> None:
        table = await tables_service.create_table(
            TableCreate(
                name="accounts",
                columns=[
                    TableColumnCreate(name="email", type=SqlType.TEXT, nullable=False),
                    TableColumnCreate(
                        name="nickname", type=SqlType.TEXT, nullable=True
                    ),
                ],
            )
        )
        full_table = await tables_service.get_table(table.id)

        await tables_service.create_unique_index(full_table, "email")
        assert await tables_service.get_index(full_table) == ["email"]

        with pytest.raises(ValueError, match="cannot have multiple unique indexes"):
            await tables_service.create_unique_index(full_table, "nickname")


@pytest.mark.anyio
class TestRowOperations:
    async def test_insert_get_and_delete_row_round_trip(
        self, tables_service: TablesService, people_table: Table
    ) -> None:
        inserted = await tables_service.insert_row(
            people_table,
            TableRowInsert(data={"name": "Bob", "age": 28}),
        )

        row_id = UUID(str(inserted["id"]))
        assert isinstance(inserted["created_at"], datetime)

        fetched = await tables_service.get_row(people_table, row_id)
        assert fetched["name"] == "Bob"
        assert fetched["age"] == 28

        await tables_service.delete_row(people_table, row_id)
        with pytest.raises(TracecatNotFoundError):
            await tables_service.get_row(people_table, row_id)

    async def test_batch_insert_rows_enforces_chunk_size(
        self, tables_service: TablesService, people_table: Table
    ) -> None:
        rows = [{"name": f"user-{idx}", "age": idx} for idx in range(1001)]

        with pytest.raises(ValueError, match="exceeds maximum"):
            await tables_service.batch_insert_rows(people_table, rows)

    async def test_batch_insert_rows_rolls_back_on_error(
        self, tables_service: TablesService, people_table: Table
    ) -> None:
        rows = [
            {"name": "Alice", "age": 30},
            {"unknown_column": "oops"},
        ]

        with pytest.raises(ValueError, match="does not exist in table"):
            await tables_service.batch_insert_rows(people_table, rows)

        assert await tables_service.list_rows(people_table) == []

    async def test_lookup_rows_returns_matching_rows(
        self, tables_service: TablesService, people_table: Table
    ) -> None:
        await tables_service.insert_row(
            people_table, TableRowInsert(data={"name": "Carol", "age": 35})
        )
        await tables_service.insert_row(
            people_table, TableRowInsert(data={"name": "Carol", "age": 29})
        )
        await tables_service.insert_row(
            people_table, TableRowInsert(data={"name": "Dan", "age": 35})
        )

        results = await tables_service.lookup_rows(
            table_name=people_table.name,
            columns=["name", "age"],
            values=["Carol", 35],
        )

        assert len(results) == 1
        match = results[0]
        assert match["name"] == "Carol"
        assert match["age"] == 35
