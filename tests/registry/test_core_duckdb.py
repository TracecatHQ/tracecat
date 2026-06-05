import json

import duckdb
import pytest
from tracecat_registry.core.duckdb import _quote, execute_sql


def test_execute_sql_returns_json_serializable_rows() -> None:
    result = execute_sql("SELECT 1 AS id, 'alpha' AS name")

    assert result == [{"id": 1, "name": "alpha"}]
    json.dumps(result)


def test_execute_sql_non_query_returns_json_serializable() -> None:
    result = execute_sql("SET TimeZone='UTC'")

    assert isinstance(result, (int, list))
    json.dumps(result)


def test_quote_escapes_single_quotes() -> None:
    assert _quote("abc") == "'abc'"
    assert _quote("a'b") == "'a''b'"
    assert _quote("Bearer ' OR 1=1 --") == "'Bearer '' OR 1=1 --'"


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
def test_headers_create_http_secret_with_escaping() -> None:
    # A header value containing a single quote must not break the CREATE SECRET
    # statement. The query confirms the http secret was created.
    result = execute_sql(
        "SELECT name FROM duckdb_secrets() WHERE type = 'http'",
        headers={"Authorization": "Bearer a'b"},
    )

    assert result == [{"name": "__tc_http"}]
