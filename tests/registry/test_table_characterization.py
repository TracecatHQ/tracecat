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

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import get_args

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import types
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.table import (
    aggregate_rows,
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
from tracecat_registry.sdk.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import get_async_session
from tracecat.db.models import Workspace
from tracecat.executor.action_gateway.app import create_app as create_action_gateway_app
from tracecat.registry.repository import Repository
from tracecat.tables.common import sanitize_identifier
from tracecat.tables.schemas import TableCreate
from tracecat.tables.service import TablesService
from tracecat.validation.common import json_schema_to_pydantic

_ACTION_GATEWAY_SOCKET = "/tmp/tracecat-test-action-gateway.sock"
app = create_action_gateway_app()


@pytest.fixture
async def table_test_role(svc_workspace: Workspace) -> Role:
    """Create a service role for table UDF tests."""
    return Role(
        type="service",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-runner",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
    )


@pytest.fixture
def gateway_requests() -> list[httpx.Request]:
    return []


@pytest.fixture
async def table_ctx(
    table_test_role: Role,
    session: AsyncSession,
    gateway_requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
):
    """Set up the ctx_role and registry context for table UDF tests.

    Routes SDK calls through the Action Gateway ASGI app.
    """
    registry_ctx = RegistryContext(
        workspace_id=str(table_test_role.workspace_id),
        workflow_id="test-workflow-id",
        run_id="test-run-id",
        environment="default",
    )
    set_context(registry_ctx)
    monkeypatch.setenv("TRACECAT__ACTION_GATEWAY_SOCKET", _ACTION_GATEWAY_SOCKET)

    class RecordingTransport(ASGITransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            gateway_requests.append(request)
            return await super().handle_async_request(request)

    def create_gateway_transport(*, uds: str) -> ASGITransport:
        assert uds == _ACTION_GATEWAY_SOCKET
        return RecordingTransport(app=app)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", create_gateway_transport)

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
        app.dependency_overrides.clear()


@pytest.fixture
async def test_table_name() -> str:
    """Generate a unique table name for each test."""
    return f"test_table_{uuid.uuid4().hex[:8]}"


@pytest.mark.anyio
@pytest.mark.dbtest
@pytest.mark.usefixtures("db", "table_ctx")
class TestAggregateRows:
    async def test_routing_syntax_never_reaches_gateway(
        self, gateway_requests: list[httpx.Request]
    ) -> None:
        for name in [
            "../workflows/00000000-0000-4000-8000-000000000001/publish#",
            "%2e%2e%2fworkflows%2fpublish%23",
            "rows?redirect=/workflows",
            "rows#fragment",
            "rows/../other",
            "rows\\other",
            "rows\n",
            ".",
            "..",
            "rows\x7f",
            "",
        ]:
            with pytest.raises(ValueError, match="Table name must"):
                await aggregate_rows(table=name, group_by=[])
        assert gateway_requests == []

    async def test_exact_legacy_names(
        self, test_table_name: str, session: AsyncSession, table_ctx: Role
    ) -> None:
        legacy_name = f"{test_table_name}-legacy café表格"
        service = TablesService(session, role=table_ctx)
        # Simulate stored metadata from before the ASCII creation restriction.
        # The physical table still follows the service's legacy normalization.
        table = await service.create_table(
            TableCreate.model_construct(name=sanitize_identifier(legacy_name))
        )
        table.name = legacy_name
        await session.flush()
        assert await aggregate_rows(table=legacy_name, group_by=[]) == {
            "groups": [{"count": 0}],
            "truncated": False,
        }

    async def test_omitted_limit_uses_server_default(
        self, test_table_name: str
    ) -> None:
        """The override tests rerun this gateway contract with fresh server settings."""
        await create_table(
            name=test_table_name, columns=[{"name": "category", "type": "TEXT"}]
        )
        await insert_rows(
            table=test_table_name,
            rows_data=[{"category": color} for color in ["blue", "green", "red"]],
        )
        result = await aggregate_rows(
            table=test_table_name,
            group_by=["category"],
            order_by="category",
            sort="asc",
        )
        expected_count = min(config.TRACECAT__LIMIT_AGG_GROUPS_DEFAULT, 3)
        assert result == {
            "groups": [
                {"category": color, "count": 1}
                for color in ["blue", "green", "red"][:expected_count]
            ],
            "truncated": expected_count < 3,
        }
        assert await aggregate_rows(
            table=test_table_name,
            group_by=["category"],
            limit=1,
            order_by="category",
            sort="asc",
        ) == {"groups": [{"category": "blue", "count": 1}], "truncated": True}
        assert await aggregate_rows(
            table=test_table_name,
            group_by=[],
            limit=config.TRACECAT__LIMIT_AGG_GROUPS_MAX,
        ) == {"groups": [{"count": 3}], "truncated": False}
        with pytest.raises(TracecatValidationError) as above_maximum:
            await aggregate_rows(
                table=test_table_name,
                group_by=[],
                limit=config.TRACECAT__LIMIT_AGG_GROUPS_MAX + 1,
            )
        assert above_maximum.value.status_code == 422

    async def test_registered_action_summarizes_filtered_rows(
        self, test_table_name: str, gateway_requests: list[httpx.Request]
    ) -> None:
        await create_table(
            name=test_table_name,
            columns=[
                {"name": "category", "type": "TEXT", "nullable": True},
                {"name": "amount", "type": "INTEGER"},
                {"name": "observed_at", "type": "TIMESTAMPTZ"},
            ],
        )
        await insert_rows(
            table=test_table_name,
            rows_data=[
                {
                    "category": "red",
                    "amount": 10,
                    "observed_at": "2026-01-02T12:00:00Z",
                },
                {
                    "category": "red",
                    "amount": 20,
                    "observed_at": "2026-01-02T13:00:00Z",
                },
                {
                    "category": "blue",
                    "amount": 5,
                    "observed_at": "2026-01-01T12:00:00Z",
                },
                {"category": None, "amount": 15, "observed_at": "2026-01-02T14:00:00Z"},
            ],
        )
        repo = Repository()
        repo._register_udf_from_function(aggregate_rows, name="aggregate_rows")
        action = repo.get("core.table.aggregate_rows")
        model = json_schema_to_pydantic(action.get_interface()["expects"])
        args = model.model_validate(
            {
                "table": test_table_name,
                "group_by": ["category"],
                "filters": {
                    "and": [
                        {"field": "amount", "op": "gt", "value": 0},
                        {"not": {"field": "amount", "op": "lt", "value": 10}},
                    ]
                },
                "aggs": [
                    {"function": "count"},
                    {"function": "sum", "field": "amount", "alias": "total"},
                ],
                "order_by": "total",
                "sort": "desc",
            }
        )
        result = await action.fn(**action.validate_args(args.model_dump()))
        assert result == {
            "groups": [
                {"category": "red", "count": 2, "total": 30.0},
                {"category": None, "count": 1, "total": 15.0},
            ],
            "truncated": False,
        }
        assert isinstance(result["groups"][0]["total"], float)
        assert isinstance(result["groups"][0]["count"], int)
        assert gateway_requests[-1].method == "POST"
        assert (
            gateway_requests[-1].url.path
            == f"/internal/tables/{test_table_name}/aggregate"
        )
        assert await aggregate_rows(table=test_table_name, group_by=[]) == {
            "groups": [{"count": 4}],
            "truncated": False,
        }
        assert await aggregate_rows(
            table=test_table_name, group_by=["category"], min_count=2, limit=1
        ) == {"groups": [{"category": "red", "count": 2}], "truncated": False}
        assert await aggregate_rows(
            table=test_table_name, group_by=["category"], limit=1
        ) == {"groups": [{"category": "red", "count": 2}], "truncated": True}
        time_args = model.model_validate(
            {
                "table": test_table_name,
                "group_by": [{"field": "observed_at", "bucket": "day"}],
            }
        )
        assert await action.fn(**action.validate_args(time_args.model_dump())) == {
            "groups": [
                {"observed_at": "2026-01-01T00:00:00Z", "count": 1},
                {"observed_at": "2026-01-02T00:00:00Z", "count": 3},
            ],
            "truncated": False,
        }
        assert await aggregate_rows(
            table=test_table_name,
            group_by=[],
            filters={"field": "amount", "op": "in", "value": []},
        ) == {"groups": [{"count": 0}], "truncated": False}

    async def test_decimal_group_keys_remain_distinct(
        self, test_table_name: str
    ) -> None:
        await create_table(
            name=test_table_name, columns=[{"name": "amount", "type": "NUMERIC"}]
        )
        await insert_rows(
            table=test_table_name,
            rows_data=[
                {"amount": "9007199254740992.1"},
                {"amount": "9007199254740992.2"},
            ],
        )
        result = await aggregate_rows(
            table=test_table_name,
            group_by=["amount"],
            order_by="amount",
            sort="asc",
        )
        assert result == {
            "groups": [
                {"amount": "9007199254740992.1", "count": 1},
                {"amount": "9007199254740992.2", "count": 1},
            ],
            "truncated": False,
        }

    async def test_validation_and_missing_table_errors(
        self, test_table_name: str
    ) -> None:
        with pytest.raises(TracecatNotFoundError) as missing:
            await aggregate_rows(table=test_table_name, group_by=[])
        assert missing.value.status_code == 404

        await create_table(name=test_table_name)
        with pytest.raises(TracecatValidationError) as semantic:
            await aggregate_rows(table=test_table_name, group_by=["missing_column"])
        assert semantic.value.status_code == 400

        with pytest.raises(TracecatValidationError) as structural:
            await aggregate_rows(table=test_table_name, group_by=[], aggs=[])
        assert structural.value.status_code == 422
        with pytest.raises(TracecatValidationError) as invalid_limit:
            await aggregate_rows(table=test_table_name, group_by=[], limit=0)
        assert invalid_limit.value.status_code == 422

    async def test_database_timeout_preserves_structured_sdk_error(
        self,
        test_table_name: str,
        session: AsyncSession,
        table_ctx: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await create_table(
            name=test_table_name, columns=[{"name": "amount", "type": "INTEGER"}]
        )
        service = TablesService(session, role=table_ctx)
        physical_table = sa.table(
            test_table_name,
            sa.column("amount", sa.BigInteger()),
            schema=service._get_schema_name(),
        )
        await session.execute(
            sa.insert(physical_table).from_select(
                ["amount"], sa.select(sa.func.generate_series(1, 200_000))
            )
        )
        monkeypatch.setattr(config, "TRACECAT__AGG_STATEMENT_TIMEOUT_MS", 1)

        with pytest.raises(TracecatValidationError) as timeout:
            await aggregate_rows(
                table=test_table_name,
                group_by=[],
                aggs=[{"function": "median", "field": "amount"}],
            )
        assert timeout.value.status_code == 422
        assert isinstance(timeout.value.detail, dict)
        assert timeout.value.detail["code"] == "query_timeout"


@pytest.mark.dbtest
@pytest.mark.slow
@pytest.mark.parametrize(("default", "maximum"), [(2, 50), (1200, 2000)])
def test_server_limit_overrides(default: int, maximum: int) -> None:
    # A fresh interpreter loads the real configured request schema and gateway.
    # The child runs only the contract test, so this cannot recursively spawn.
    # tests.database gives each process its own randomly named database.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/registry/test_table_characterization.py::TestAggregateRows::test_omitted_limit_uses_server_default",
            "-o",
            "addopts=",
            "-n",
            "0",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "TRACECAT__LIMIT_AGG_GROUPS_DEFAULT": str(default),
            "TRACECAT__LIMIT_AGG_GROUPS_MAX": str(maximum),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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

    async def test_update_row_preserves_null_for_text_column(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """Updating a TEXT column to None stores SQL NULL, not the string "None"."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)
        inserted = await insert_row(
            table=test_table_name,
            row_data={"email": "test@example.com"},
        )

        result = await update_row(
            table=test_table_name,
            row_id=str(inserted["id"]),
            row_data={"email": None},
        )

        assert result["email"] is None


# =============================================================================
# insert_row / insert_rows consistency characterization tests
# =============================================================================


@pytest.mark.anyio
class TestInsertRowNullTextConsistency:
    """insert_row and insert_rows must agree on how a null TEXT value is stored."""

    async def test_insert_row_preserves_null_for_text_column(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """insert_row with a None TEXT value stores SQL NULL, not the string "None"."""
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        result = await insert_row(
            table=test_table_name,
            row_data={"email": None},
        )

        assert result["email"] is None

    async def test_insert_row_and_insert_rows_agree_on_null_text(
        self, db, session: AsyncSession, table_ctx: Role, test_table_name: str
    ):
        """insert_row and insert_rows must store the same value for a None TEXT
        column. Before the fix, insert_row stored the string "None" while
        insert_rows (batch_insert_rows) stored real SQL NULL for the same input.
        """
        columns = [{"name": "email", "type": "TEXT"}]
        await create_table(name=test_table_name, columns=columns)

        single = await insert_row(table=test_table_name, row_data={"email": None})
        await insert_rows(table=test_table_name, rows_data=[{"email": None}])

        rows = await search_rows(table=test_table_name)
        assert isinstance(rows, list)
        batch_row = next(r for r in rows if r["id"] != single["id"])

        assert single["email"] is None
        assert batch_row["email"] is None


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
