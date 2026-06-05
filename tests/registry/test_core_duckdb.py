import json

import duckdb
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


def _httpfs_available() -> bool:
    con = duckdb.connect()
    try:
        con.execute("LOAD httpfs")
        return True
    except Exception:
        return False
    finally:
        con.close()


@pytest.mark.skipif(
    not _httpfs_available(),
    reason="httpfs extension not available (no preinstalled extensions or network)",
)
def test_headers_bound_as_parameters_not_interpolated() -> None:
    # A header value full of SQL metacharacters must be stored verbatim as data,
    # never parsed as SQL. We read it back from the created secret to prove the
    # value round-trips untouched (i.e. injection is impossible by design).
    token = "Bearer a'b; DROP SECRET __tc_http; --"
    result = execute_sql(
        "SELECT name, secret_string FROM duckdb_secrets() WHERE type = 'http'",
        headers={"Authorization": token},
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "__tc_http"
    # The injected SQL fragment survives as data inside the stored header map
    # (the secret still exists and the DROP was never executed).
    assert "DROP SECRET __tc_http" in result[0]["secret_string"]
