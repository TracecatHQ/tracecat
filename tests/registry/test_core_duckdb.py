import json

import duckdb
import pytest
from tracecat_registry.core import duckdb as duckdb_action
from tracecat_registry.core.duckdb import (
    _extension_directory,
    _normalize_headers_scope,
    execute_sql,
)


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


def _httpfs_available() -> bool:
    con = duckdb.connect()
    try:
        con.execute("LOAD httpfs")
        return True
    except Exception:
        return False
    finally:
        con.close()


def test_headers_require_scope() -> None:
    # headers without headers_scope is rejected before any DuckDB work, so this
    # runs without the httpfs extension.
    with pytest.raises(ValueError, match="headers_scope is required"):
        execute_sql("SELECT 1", headers={"Authorization": "Bearer x"})


def test_headers_scope_must_be_http_url() -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        execute_sql(
            "SELECT 1",
            headers={"Authorization": "Bearer x"},
            headers_scope="api.example.com",
        )


def test_normalize_headers_scope() -> None:
    # Bare host prefix gets a trailing slash to anchor the host boundary, so it
    # cannot prefix-match a sibling host (api.example.com.evil.com).
    assert _normalize_headers_scope("https://api.example.com") == (
        "https://api.example.com/"
    )
    assert _normalize_headers_scope("https://api.example.com/") == (
        "https://api.example.com/"
    )
    # An explicit path prefix is preserved.
    assert _normalize_headers_scope("https://api.example.com/v1") == (
        "https://api.example.com/v1"
    )
    assert _normalize_headers_scope("http://host:9000") == "http://host:9000/"
    # Query/fragment are dropped.
    assert _normalize_headers_scope("https://h/p?q=1#f") == "https://h/p"


def test_normalize_headers_scope_rejects_invalid() -> None:
    for bad in (None, "", "api.example.com", "ftp://host", "https://", "/just/a/path"):
        with pytest.raises(ValueError):
            _normalize_headers_scope(bad)


@pytest.mark.skipif(
    not _httpfs_available(),
    reason="httpfs extension not available (no preinstalled extensions or network)",
)
def test_headers_bound_as_parameters_not_interpolated() -> None:
    # A header value full of SQL metacharacters must be stored verbatim as data,
    # never parsed as SQL. We read it back from the created secret to prove the
    # value round-trips untouched (i.e. injection is impossible by design), and
    # that the scope is applied so headers are restricted to the intended host.
    token = "Bearer a'b; DROP SECRET __tc_http; --"
    result = execute_sql(
        "SELECT name, scope, secret_string FROM duckdb_secrets() WHERE type = 'http'",
        headers={"Authorization": token},
        headers_scope="https://api.example.com",
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "__tc_http"
    # The injected SQL fragment survives as data inside the stored header map
    # (the secret still exists and the DROP was never executed).
    assert "DROP SECRET __tc_http" in result[0]["secret_string"]
    # The scope restricts the headers to the intended host (normalized + anchored).
    assert result[0]["scope"] == ["https://api.example.com/"]
