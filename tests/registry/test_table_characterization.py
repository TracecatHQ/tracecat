"""Characterization tests for core.table UDFs.

These tests verify the behavior of table UDFs as black boxes, using real database
operations. They serve as regression tests for the SDK migration - the same tests
should pass before and after migration.

Test Strategy:
- No mocks - tests exercise the full path through the service layer
- Tests assert on inputs → outputs of UDFs
- Implementation details (direct service calls vs SDK) are abstracted away
"""

from __future__ import annotations

import uuid
from typing import get_args

import pytest
import respx
from httpx import ASGITransport
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import types
from tracecat_registry.context import RegistryContext, clear_context, set_context
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

from tracecat import config
from tracecat.api.app import app
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import get_async_session
from tracecat.db.models import Workspace


@pytest.fixture
async def table_test_role(svc_workspace: Workspace) -> Role:
    """Create a service role for table UDF tests."""
    return Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )


@pytest.fixture
async def table_ctx(
    table_test_role: Role,
    session: AsyncSession,
):
    """Set up the ctx_role and registry context for table UDF tests.

    Uses SDK path with respx mock to route HTTP calls to the FastAPI app.
    """
    registry_ctx = RegistryContext(
        workspace_id=str(table_test_role.workspace_id),
        workflow_id="test-workflow-id",
        run_id="test-run-id",
        environment="default",
        api_url=config.TRACECAT__API_URL,
    )
    set_context(registry_ctx)

    # Set up respx mock to route SDK HTTP calls to the FastAPI app
    respx_mock = respx.mock(assert_all_mocked=False, assert_all_called=False)
    respx_mock.start()
    respx_mock.route(url__startswith=config.TRACECAT__API_URL).mock(
        side_effect=ASGITransport(app).handle_async_request
    )

    def override_role():
        return table_test_role

    metadata = get_args(ExecutorWorkspaceRole)
    if len(metadata) > 1 and hasattr(metadata[1], "dependency"):
        app.dependency_overrides[metadata[1].dependency] = override_role

    async def override_get_async_session():
        yield session

    app.dependency_overrides[get_async_session] = override_get_async_session

    token = ctx_role.set(table_test_role)
    try:
        yield table_test_role
    finally:
        ctx_role.reset(token)
        clear_context()
        respx_mock.stop()
        app.dependency_overrides.clear()


@pytest.fixture
async def test_table_name() -> str:
    """Generate a unique table name for each test."""
    return f"test_table_{uuid.uuid4().hex[:8]}"


# =============================================================================
# create_table characterization tests
# =============================================================================


@pytest.mark.anyio
class TestCreateTable:
    """Characterization tests for create_table UDF."""

    async def test_create_table_basic(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Create a table with no columns returns table metadata."""
        result = await create_table(name=test_table_name)

        # Validate against SDK type
        TypeAdapter(types.Table).validate_python(result)

        assert result["name"] == test_table_name
        assert "id" in result
        assert "created_at" in result
        assert "updated_at" in result

    async def test_create_table_with_columns(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Create a table with column definitions."""
        # Note: Valid SqlTypes are TEXT, INTEGER, NUMERIC, DATE, BOOLEAN,
        # TIMESTAMP, TIMESTAMPTZ, JSONB, UUID, SELECT, MULTI_SELECT
        columns = [
            {"name": "email", "type": "TEXT", "nullable": False},
            {"name": "age", "type": "INTEGER", "nullable": True},
            {"name": "score", "type": "NUMERIC", "nullable": True},
        ]

        result = await create_table(name=test_table_name, columns=columns)

        # Validate against SDK type
        TypeAdapter(types.Table).validate_python(result)

        assert result["name"] == test_table_name
        assert "id" in result

    async def test_create_table_duplicate_raises_by_default(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Creating duplicate table raises ValueError by default."""
        await create_table(name=test_table_name)

        with pytest.raises(ValueError, match="Table already exists"):
            await create_table(name=test_table_name)

    async def test_create_table_duplicate_no_raise(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Creating duplicate table with raise_on_duplicate=False returns existing."""
        result1 = await create_table(name=test_table_name)
        result2 = await create_table(name=test_table_name, raise_on_duplicate=False)

        assert result1["id"] == result2["id"]
        assert result1["name"] == result2["name"]


# =============================================================================
# list_tables characterization tests
# =============================================================================


@pytest.mark.anyio
class TestListTables:
    """Characterization tests for list_tables UDF."""

    async def test_list_tables_empty(self, db, session: AsyncSession, table_ctx: Role):
        """List tables returns empty list when no tables exist."""
        result = await list_tables()

        # Validate against SDK type
        TypeAdapter(list[types.Table]).validate_python(result)

        # Result should be a list (may contain tables from other tests)
        assert isinstance(result, list)

    async def test_list_tables_includes_created(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """List tables includes newly created table."""
        await create_table(name=test_table_name)

        result = await list_tables()

        table_names = [t["name"] for t in result]
        assert test_table_name in table_names


# =============================================================================
# get_table_metadata characterization tests
# =============================================================================


@pytest.mark.anyio
class TestGetTableMetadata:
    """Characterization tests for get_table_metadata UDF."""

    async def test_get_table_metadata_basic(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Get metadata for a table with columns."""
        columns = [
            {"name": "email", "type": "TEXT", "nullable": False},
            {"name": "count", "type": "INTEGER", "nullable": True},
        ]
        await create_table(name=test_table_name, columns=columns)

        result = await get_table_metadata(name=test_table_name)

        # Validate against SDK type
        TypeAdapter(types.TableRead).validate_python(result)

        assert result["name"] == test_table_name
        assert "columns" in result
        column_names = [c["name"] for c in result["columns"]]
        assert "email" in column_names
        assert "count" in column_names


# =============================================================================
# insert_row characterization tests
# =============================================================================


@pytest.mark.anyio
class TestInsertRow:
    """Characterization tests for insert_row UDF."""

    async def test_insert_row_returns_row_with_id(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Insert row returns the inserted row with generated ID."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        result = await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )

        assert "id" in result
        assert result["email"] == "test@example.com"
        assert "created_at" in result
        assert "updated_at" in result

    async def test_insert_row_multiple_columns(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Insert row with multiple column values."""
        columns = [
            {"name": "name", "type": "TEXT"},
            {"name": "age", "type": "INTEGER"},
        ]
        await create_table(name=test_table_name, columns=columns)

        result = await insert_row(
            table=test_table_name,
            row_data={"name": "John Doe", "age": 30},
        )

        assert result["name"] == "John Doe"
        assert result["age"] == 30


# =============================================================================
# insert_rows characterization tests
# =============================================================================


@pytest.mark.anyio
class TestInsertRows:
    """Characterization tests for insert_rows UDF."""

    async def test_insert_rows_returns_count(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Insert multiple rows returns the count of inserted rows."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        rows = [
            {"email": "user1@example.com"},
            {"email": "user2@example.com"},
            {"email": "user3@example.com"},
        ]
        result = await insert_rows(table=test_table_name, rows_data=rows)

        assert result == 3

    async def test_insert_rows_empty_list(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Insert empty list returns 0."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        result = await insert_rows(table=test_table_name, rows_data=[])

        assert result == 0


# =============================================================================
# lookup characterization tests
# =============================================================================


@pytest.mark.anyio
class TestLookup:
    """Characterization tests for lookup UDF."""

    async def test_lookup_found(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Lookup returns matching row when found."""
        columns = [
            {"name": "email", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
        ]
        await create_table(name=test_table_name, columns=columns)
        await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com", "name": "Test User"},
        )

        result = await lookup(
            table=test_table_name,
            column="email",
            value="test@example.com",
        )

        assert result is not None
        assert result["email"] == "test@example.com"
        assert result["name"] == "Test User"

    async def test_lookup_not_found(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Lookup returns None when no match found."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        result = await lookup(
            table=test_table_name,
            column="email",
            value="nonexistent@example.com",
        )

        assert result is None

    async def test_lookup_returns_first_match(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Lookup returns only first match when multiple exist."""
        columns = [
            {"name": "status", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
        ]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[
                {"status": "active", "name": "User 1"},
                {"status": "active", "name": "User 2"},
            ],
        )

        result = await lookup(
            table=test_table_name,
            column="status",
            value="active",
        )

        # Should return exactly one result
        assert result is not None
        assert result["status"] == "active"


# =============================================================================
# lookup_many characterization tests
# =============================================================================


@pytest.mark.anyio
class TestLookupMany:
    """Characterization tests for lookup_many UDF."""

    async def test_lookup_many_returns_all_matches(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Lookup many returns all matching rows."""
        columns = [
            {"name": "status", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
        ]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[
                {"status": "active", "name": "User 1"},
                {"status": "active", "name": "User 2"},
                {"status": "inactive", "name": "User 3"},
            ],
        )

        result = await lookup_many(
            table=test_table_name,
            column="status",
            value="active",
        )

        assert len(result) == 2
        assert all(r["status"] == "active" for r in result)

    async def test_lookup_many_empty_result(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Lookup many returns empty list when no matches."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        result = await lookup_many(
            table=test_table_name,
            column="email",
            value="nonexistent@example.com",
        )

        assert result == []

    async def test_lookup_many_respects_limit(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Lookup many respects the limit parameter."""
        columns = [{"name": "status", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[{"status": "active"} for _ in range(10)],
        )

        result = await lookup_many(
            table=test_table_name,
            column="status",
            value="active",
            limit=5,
        )

        assert len(result) == 5


# =============================================================================
# is_in characterization tests
# =============================================================================


@pytest.mark.anyio
class TestIsIn:
    """Characterization tests for is_in UDF."""

    async def test_is_in_returns_true_when_exists(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """is_in returns True when value exists in table."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )

        result = await is_in(
            table=test_table_name,
            column="email",
            value="test@example.com",
        )

        assert result is True

    async def test_is_in_returns_false_when_not_exists(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """is_in returns False when value does not exist."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        result = await is_in(
            table=test_table_name,
            column="email",
            value="nonexistent@example.com",
        )

        assert result is False


# =============================================================================
# update_row characterization tests
# =============================================================================


@pytest.mark.anyio
class TestUpdateRow:
    """Characterization tests for update_row UDF."""

    async def test_update_row_modifies_data(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Update row modifies the specified row data."""
        columns = [
            {"name": "email", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
        ]
        await create_table(name=test_table_name, columns=columns)
        inserted = await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com", "name": "Old Name"},
        )

        result = await update_row(
            table=test_table_name,
            row_id=str(inserted["id"]),  # Convert UUID to string
            row_data={"name": "New Name"},
        )

        assert result["name"] == "New Name"
        assert result["email"] == "test@example.com"  # Unchanged

    async def test_update_row_updates_timestamp(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Update row updates the updated_at timestamp."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        inserted = await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )
        original_updated_at = inserted["updated_at"]

        result = await update_row(
            table=test_table_name,
            row_id=str(inserted["id"]),  # Convert UUID to string
            row_data={"email": "updated@example.com"},
        )

        # updated_at should be >= original (may be same if very fast)
        assert result["updated_at"] >= original_updated_at


# =============================================================================
# delete_row characterization tests
# =============================================================================


@pytest.mark.anyio
class TestDeleteRow:
    """Characterization tests for delete_row UDF."""

    async def test_delete_row_removes_row(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Delete row removes the row from the table."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        inserted = await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )

        await delete_row(
            table=test_table_name, row_id=str(inserted["id"])
        )  # Convert UUID to string

        # Verify row is gone
        result = await lookup(
            table=test_table_name,
            column="email",
            value="test@example.com",
        )
        assert result is None


# =============================================================================
# search_rows characterization tests
# =============================================================================


@pytest.mark.anyio
class TestSearchRows:
    """Characterization tests for search_rows UDF."""

    async def test_search_rows_by_text(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Search rows finds rows containing search term."""
        columns = [
            {"name": "title", "type": "TEXT"},
            {"name": "description", "type": "TEXT"},
        ]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[
                {"title": "Security Alert", "description": "Suspicious login detected"},
                {"title": "System Update", "description": "Patch applied successfully"},
                {"title": "Security Patch", "description": "Critical update installed"},
            ],
        )

        result = await search_rows(
            table=test_table_name,
            search_term="Security",
        )

        assert isinstance(result, list)
        assert len(result) == 2
        titles = [r["title"] for r in result]
        assert "Security Alert" in titles
        assert "Security Patch" in titles

    async def test_search_rows_with_limit(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Search rows respects limit parameter."""
        columns = [{"name": "name", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[{"name": f"Test Item {i}"} for i in range(10)],
        )

        result = await search_rows(
            table=test_table_name,
            search_term="Test",
            limit=3,
        )

        assert isinstance(result, list)
        assert len(result) == 3

    async def test_search_rows_no_matches(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Search rows returns empty list when no matches."""
        columns = [{"name": "name", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_row(
            table=test_table_name,
            row_data={"name": "Test Item"},
        )

        result = await search_rows(
            table=test_table_name,
            search_term="Nonexistent",
        )

        assert isinstance(result, list)
        assert result == []


# =============================================================================
# download characterization tests
# =============================================================================


@pytest.mark.anyio
class TestDownload:
    """Characterization tests for download UDF."""

    async def test_download_returns_list_by_default(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Download with no format returns list of dicts."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[
                {"email": "user1@example.com"},
                {"email": "user2@example.com"},
            ],
        )

        result = await download(name=test_table_name)

        assert isinstance(result, list)
        assert len(result) == 2
        emails = [r["email"] for r in result]
        assert "user1@example.com" in emails
        assert "user2@example.com" in emails

    async def test_download_json_format(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Download with json format returns JSON string."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )

        result = await download(name=test_table_name, format="json")

        assert isinstance(result, str)
        assert "test@example.com" in result

    async def test_download_csv_format(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Download with csv format returns CSV string."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )

        result = await download(name=test_table_name, format="csv")

        assert isinstance(result, str)
        assert "email" in result  # Header
        assert "test@example.com" in result

    async def test_download_respects_limit(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Download respects limit parameter."""
        columns = [{"name": "num", "type": "INTEGER"}]
        await create_table(name=test_table_name, columns=columns)
        await insert_rows(
            table=test_table_name,
            rows_data=[{"num": i} for i in range(10)],
        )

        result = await download(name=test_table_name, limit=5)

        assert isinstance(result, list)
        assert len(result) == 5
