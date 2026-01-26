from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from tracecat_registry._internal import secrets as registry_secrets
from tracecat_registry.core.sql import (
    SQLConnectionValidationError,
    _validate_connection_url,
    execute_query,
)

from tests.database import TEST_DB_CONFIG


@contextmanager
def registry_secrets_sandbox(secrets: dict[str, str]):
    """Context manager that sets up the registry secrets context.

    This is needed because the registry secrets module reads from its own
    context variable, not from environment variables directly.
    """
    token = registry_secrets.set_context(secrets)
    try:
        yield
    finally:
        registry_secrets.reset_context(token)


def test_validate_connection_url_blocks_internal_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject user URLs that target the configured internal DB endpoint/port."""
    from tracecat_registry import config as registry_config

    monkeypatch.setattr(
        registry_config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@internal-db:5432/tracecat",
    )
    monkeypatch.setattr(registry_config, "TRACECAT__DB_ENDPOINT", "internal-db")
    monkeypatch.setattr(registry_config, "TRACECAT__DB_PORT", "5432")

    connection_url = make_url(
        "postgresql+psycopg://user:pass@internal-db:5432/external_db"
    )

    with pytest.raises(SQLConnectionValidationError) as excinfo:
        _validate_connection_url(connection_url)

    message = str(excinfo.value).lower()
    assert "internal database endpoint" in message
    assert "secret" not in message


def test_validate_connection_url_allows_external_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow user URLs that point to a different endpoint."""
    from tracecat_registry import config as registry_config

    monkeypatch.setattr(
        registry_config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@internal-db:5432/tracecat",
    )
    monkeypatch.setattr(registry_config, "TRACECAT__DB_ENDPOINT", "internal-db")
    monkeypatch.setattr(registry_config, "TRACECAT__DB_PORT", "5432")

    connection_url = make_url(
        "postgresql+psycopg://user:pass@external-db:5432/external_db"
    )

    _validate_connection_url(connection_url)  # Should not raise


def test_validate_connection_url_uses_internal_uri_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback to TRACECAT__DB_URI when TRACECAT__DB_ENDPOINT is unset."""
    from tracecat_registry import config as registry_config

    monkeypatch.setattr(
        registry_config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@postgres_db:6432/tracecat",
    )
    monkeypatch.setattr(registry_config, "TRACECAT__DB_ENDPOINT", None)
    monkeypatch.setattr(registry_config, "TRACECAT__DB_PORT", None)

    connection_url = make_url(
        "postgresql+psycopg://user:pass@postgres_db:6432/external_db"
    )

    with pytest.raises(SQLConnectionValidationError):
        _validate_connection_url(connection_url)


# Integration tests using live Postgres database
@pytest.fixture
def setup_sql_test_table(db, monkeypatch: pytest.MonkeyPatch):  # noqa: ARG001
    """Set up test table and mock config for SQL integration tests.

    Mocks the internal database config to use a different endpoint
    so the test database on localhost:5432 is allowed by validation.
    """
    from tracecat_registry import config as registry_config

    # Mock internal database config to use a different endpoint
    # This allows the test database on localhost:5432 to pass validation
    monkeypatch.setattr(
        registry_config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@internal-db:5432/tracecat",
    )
    monkeypatch.setattr(registry_config, "TRACECAT__DB_ENDPOINT", "internal-db")
    monkeypatch.setattr(registry_config, "TRACECAT__DB_PORT", "5432")

    # Create test table and insert sample data
    engine = create_engine(TEST_DB_CONFIG.test_url_sync)
    with engine.begin() as conn:
        # Create test table
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS test_users (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    age INTEGER,
                    active BOOLEAN DEFAULT TRUE
                )
                """
            )
        )
        # Insert test data
        conn.execute(
            text(
                """
                INSERT INTO test_users (name, email, age, active)
                VALUES
                    ('Alice', 'alice@example.com', 30, TRUE),
                    ('Bob', 'bob@example.com', 25, TRUE),
                    ('Charlie', 'charlie@example.com', 35, FALSE),
                    ('Diana', 'diana@example.com', 28, TRUE),
                    ('Eve', 'eve@example.com', 32, TRUE)
                ON CONFLICT DO NOTHING
                """
            )
        )

    yield

    # Cleanup
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS test_users"))
    engine.dispose()


@pytest.mark.anyio
async def test_execute_query_select_all(setup_sql_test_table):
    """Test SELECT query returning all rows."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "SELECT id, name, email, age, active FROM test_users ORDER BY id"
        )

        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0]["name"] == "Alice"
        assert result[0]["email"] == "alice@example.com"
        assert result[0]["age"] == 30
        assert result[0]["active"] is True


@pytest.mark.anyio
async def test_execute_query_select_with_where(setup_sql_test_table):
    """Test SELECT query with WHERE clause."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "SELECT name, email FROM test_users WHERE active = :active",
            bound_params={"active": True},
        )

        assert isinstance(result, list)
        assert len(result) == 4  # 4 active users
        assert all(row["name"] in ["Alice", "Bob", "Diana", "Eve"] for row in result)


@pytest.mark.anyio
async def test_execute_query_fetch_one(setup_sql_test_table):
    """Test SELECT query with fetch_one=True."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "SELECT name, email FROM test_users WHERE age > :min_age ORDER BY age LIMIT 1",
            bound_params={"min_age": 30},
            fetch_one=True,
        )

        assert isinstance(result, dict)
        # Users with age > 30: Charlie (35), Eve (32). Ordered by age, Eve comes first.
        assert result["name"] == "Eve"
        assert result["email"] == "eve@example.com"


@pytest.mark.anyio
async def test_execute_query_fetch_one_no_results(setup_sql_test_table):
    """Test SELECT query with fetch_one=True when no results."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "SELECT name, email FROM test_users WHERE age > :min_age",
            bound_params={"min_age": 100},
            fetch_one=True,
        )

        assert result is None


@pytest.mark.anyio
async def test_execute_query_insert(setup_sql_test_table):
    """Test INSERT query returning rowcount."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "INSERT INTO test_users (name, email, age, active) VALUES (:name, :email, :age, :active)",
            bound_params={
                "name": "Frank",
                "email": "frank@example.com",
                "age": 40,
                "active": True,
            },
        )

        assert isinstance(result, int)
        assert result == 1

        # Verify the insert
        verify = await execute_query(
            "SELECT name FROM test_users WHERE email = :email",
            bound_params={"email": "frank@example.com"},
            fetch_one=True,
        )
        assert verify is not None
        assert isinstance(verify, dict)
        assert verify["name"] == "Frank"


@pytest.mark.anyio
async def test_execute_query_update(setup_sql_test_table):
    """Test UPDATE query returning rowcount."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "UPDATE test_users SET age = :new_age WHERE name = :name",
            bound_params={"new_age": 31, "name": "Alice"},
        )

        assert isinstance(result, int)
        assert result == 1

        # Verify the update
        verify = await execute_query(
            "SELECT age FROM test_users WHERE name = :name",
            bound_params={"name": "Alice"},
            fetch_one=True,
        )
        assert verify is not None
        assert isinstance(verify, dict)
        assert verify["age"] == 31


@pytest.mark.anyio
async def test_execute_query_delete(setup_sql_test_table):
    """Test DELETE query returning rowcount."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "DELETE FROM test_users WHERE name = :name", bound_params={"name": "Eve"}
        )

        assert isinstance(result, int)
        assert result == 1

        # Verify the delete
        verify = await execute_query(
            "SELECT name FROM test_users WHERE name = :name",
            bound_params={"name": "Eve"},
            fetch_one=True,
        )
        assert verify is None


@pytest.mark.anyio
async def test_execute_query_max_rows_limit(setup_sql_test_table):
    """Test that max_rows limits the number of returned rows."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "SELECT id, name FROM test_users ORDER BY id", max_rows=2
        )

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "Alice"
        assert result[1]["name"] == "Bob"


@pytest.mark.anyio
async def test_execute_query_parameterized_query(setup_sql_test_table):
    """Test parameterized query with multiple parameters."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            """
            SELECT name, email FROM test_users
            WHERE age BETWEEN :min_age AND :max_age AND active = :active
            ORDER BY name
            """,
            bound_params={"min_age": 25, "max_age": 30, "active": True},
        )

        assert isinstance(result, list)
        # Should return Alice (30), Bob (25), Diana (28)
        assert len(result) == 3
        names = [row["name"] for row in result]
        assert "Alice" in names
        assert "Bob" in names
        assert "Diana" in names


@pytest.mark.anyio
async def test_execute_query_invalid_sql(setup_sql_test_table):
    """Test that invalid SQL raises an exception."""
    from sqlalchemy.exc import SQLAlchemyError

    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        with pytest.raises(SQLAlchemyError):
            await execute_query("SELECT * FROM nonexistent_table")


@pytest.mark.anyio
async def test_execute_query_invalid_connection_url(monkeypatch: pytest.MonkeyPatch):
    """Test that invalid connection URL raises an error."""
    from tracecat_registry import config as registry_config

    # Mock internal database config to avoid validation issues
    monkeypatch.setattr(
        registry_config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@internal-db:5432/tracecat",
    )
    monkeypatch.setattr(registry_config, "TRACECAT__DB_ENDPOINT", "internal-db")
    monkeypatch.setattr(registry_config, "TRACECAT__DB_PORT", "5432")

    # The error will be NoSuchModuleError from SQLAlchemy
    from sqlalchemy.exc import NoSuchModuleError

    with registry_secrets_sandbox({"CONNECTION_URL": "invalid://url"}):
        with pytest.raises((ValueError, NoSuchModuleError)):
            await execute_query("SELECT 1")


@pytest.mark.anyio
async def test_execute_query_no_bound_params(setup_sql_test_table):
    """Test query without bound parameters."""
    connection_url = TEST_DB_CONFIG.test_url_sync
    with registry_secrets_sandbox({"CONNECTION_URL": connection_url}):
        result = await execute_query(
            "SELECT COUNT(*) as total FROM test_users WHERE active = TRUE"
        )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["total"] == 4
