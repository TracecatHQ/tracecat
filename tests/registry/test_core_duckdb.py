import json

import pytest
from tracecat_registry.core import duckdb as duckdb_action
from tracecat_registry.core.duckdb import _extension_directory, execute_sql


def test_execute_sql_returns_json_serializable_rows() -> None:
    result = execute_sql("SELECT 1 AS id, 'alpha' AS name")

    assert result == [{"id": 1, "name": "alpha"}]
    json.dumps(result)


def test_execute_sql_non_query_returns_json_serializable() -> None:
    result = execute_sql("SET TimeZone='UTC'")

    assert isinstance(result, (int, list))
    json.dumps(result)


def test_extension_directory_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        duckdb_action, "TRACECAT__DUCKDB_EXTENSION_DIRECTORY", "/custom/ext"
    )
    assert _extension_directory() == "/custom/ext"


def test_extension_directory_falls_back_to_default_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(duckdb_action, "TRACECAT__DUCKDB_EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(duckdb_action, "_DEFAULT_EXTENSION_DIRECTORY", str(tmp_path))
    assert _extension_directory() == str(tmp_path)


def test_extension_directory_none_when_unset_and_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(duckdb_action, "TRACECAT__DUCKDB_EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(
        duckdb_action, "_DEFAULT_EXTENSION_DIRECTORY", str(tmp_path / "missing")
    )
    assert _extension_directory() is None
