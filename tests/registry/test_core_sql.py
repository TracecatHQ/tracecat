"""Tests for core.sql actions (standalone, no live DB)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url
from tracecat_registry import secrets
from tracecat_registry.core import sql as core_sql


def test_validate_connection_url_blocks_internal_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core_sql,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@internal-db:5432/tracecat",
    )
    monkeypatch.setattr(core_sql, "TRACECAT__DB_ENDPOINT", "internal-db")
    monkeypatch.setattr(core_sql, "TRACECAT__DB_PORT", "5432")

    with pytest.raises(core_sql.SQLConnectionValidationError) as excinfo:
        core_sql._validate_connection_url(
            make_url("postgresql+psycopg://user:pass@internal-db:5432/external_db")
        )
    assert "internal database endpoint" in str(excinfo.value).lower()
    assert "secret" not in str(excinfo.value).lower()


def test_validate_connection_url_allows_external_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core_sql,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://tracecat:secret@internal-db:5432/tracecat",
    )
    monkeypatch.setattr(core_sql, "TRACECAT__DB_ENDPOINT", "internal-db")
    monkeypatch.setattr(core_sql, "TRACECAT__DB_PORT", "5432")

    core_sql._validate_connection_url(
        make_url("postgresql+psycopg://user:pass@external-db:5432/external_db")
    )


def _fake_engine(
    *,
    returns_rows: bool,
    rows: list[dict[str, Any]] | None = None,
    rowcount: int = 0,
) -> MagicMock:
    engine = MagicMock()
    conn = MagicMock()
    result = MagicMock()
    result.returns_rows = returns_rows
    result.rowcount = rowcount

    mappings = MagicMock()
    if rows is None:
        rows = []

    mappings.fetchone.return_value = rows[0] if rows else None
    mappings.fetchmany.return_value = rows
    result.mappings.return_value = mappings
    conn.execute.return_value = result

    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = None
    engine.begin.return_value = cm
    return engine


@pytest.mark.anyio
async def test_execute_query_fetches_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        core_sql,
        "_create_engine_with_validation",
        lambda _url: _fake_engine(
            returns_rows=True,
            rows=[{"name": "Alice"}, {"name": "Bob"}],
        ),
    )
    with secrets.env_sandbox({"CONNECTION_URL": "postgresql+psycopg://u:p@db:5432/x"}):
        result = await core_sql.execute_query("SELECT name FROM users")

    assert result == [{"name": "Alice"}, {"name": "Bob"}]


@pytest.mark.anyio
async def test_execute_query_fetch_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        core_sql,
        "_create_engine_with_validation",
        lambda _url: _fake_engine(returns_rows=True, rows=[{"id": 1}]),
    )
    with secrets.env_sandbox({"CONNECTION_URL": "postgresql+psycopg://u:p@db:5432/x"}):
        result = await core_sql.execute_query("SELECT id FROM users", fetch_one=True)
    assert result == {"id": 1}


@pytest.mark.anyio
async def test_execute_query_fetch_one_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        core_sql,
        "_create_engine_with_validation",
        lambda _url: _fake_engine(returns_rows=True, rows=[]),
    )
    with secrets.env_sandbox({"CONNECTION_URL": "postgresql+psycopg://u:p@db:5432/x"}):
        result = await core_sql.execute_query("SELECT id FROM users", fetch_one=True)
    assert result is None


@pytest.mark.anyio
async def test_execute_query_returns_rowcount_for_nonselect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core_sql,
        "_create_engine_with_validation",
        lambda _url: _fake_engine(returns_rows=False, rowcount=3),
    )
    with secrets.env_sandbox({"CONNECTION_URL": "postgresql+psycopg://u:p@db:5432/x"}):
        result = await core_sql.execute_query("UPDATE users SET active = true")
    assert result == 3
