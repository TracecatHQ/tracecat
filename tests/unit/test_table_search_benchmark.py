"""Benchmark invariants that protect setup and the declared query contract."""

from unittest.mock import Mock

import pytest

from scripts.benchmark.table_search.queries import Search, query
from scripts.benchmark.table_search.run import percentiles, validate_database
from scripts.benchmark.table_search.schema import columns, insert_sql


def test_database_guard_rejects_application_database_before_catalog_scan() -> None:
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = ("postgres",)
    with pytest.raises(ValueError, match="Database name"):
        validate_database(conn)
    assert conn.execute.call_count == 1


def test_database_guard_rejects_existing_user_relations() -> None:
    conn = Mock()
    conn.execute.return_value.fetchone.side_effect = [
        ("tracecat_search_bench_synthetic",),
        (1,),
    ]
    with pytest.raises(ValueError, match="no user relations"):
        validate_database(conn)


def test_database_guard_accepts_empty_isolated_database() -> None:
    conn = Mock()
    conn.execute.return_value.fetchone.side_effect = [
        ("tracecat_search_bench_synthetic",),
        (0,),
    ]
    validate_database(conn)


@pytest.mark.parametrize("projection", [False, True])
def test_queries_reject_missing_selected_columns(projection: bool) -> None:
    with pytest.raises(ValueError, match="missing"):
        query(
            columns(4),
            Search("missing", "alpha", selected=("text_0", "missing")),
            projection=projection,
        )


@pytest.mark.parametrize("projection", [False, True])
def test_queries_bind_injection_text_and_escape_literal_wildcards(
    projection: bool,
) -> None:
    value = "literal_%' OR 1=1 --"
    statement, params = query(
        columns(4), Search("literal", value, "trgm"), projection=projection
    )
    assert value not in statement
    assert params[0] == "%literal\\_\\%' OR 1=1 --%"


@pytest.mark.parametrize("projection", [False, True])
def test_full_text_consumes_phrase_contract_and_pages_stably(projection: bool) -> None:
    statement, params = query(
        columns(4),
        Search("terms", 'alpha "red fox"'),
        projection=projection,
        cursor=(0.5, 123),
    )
    assert params == ("alpha", "red fox", 0.5, 123)
    assert "phraseto_tsquery('pg_catalog.simple'" in statement
    assert "plainto_tsquery('pg_catalog.simple'" in statement
    assert "ORDER BY score DESC, id DESC LIMIT 50" in statement
    assert "(score, id) < (%s::real, %s::bigint)" in statement


def test_direct_json_phrases_recheck_individual_values() -> None:
    statement, _ = query(columns(4), Search("json", '"red fox"', selected=("payload",)))
    assert "EXISTS (SELECT 1 FROM json_values(payload)" in statement


def test_dataset_is_seeded_and_spans_supported_types() -> None:
    cols = columns(16)
    assert len(cols) == 16
    assert {c.name for c in cols} >= {"text_0", "payload", "choice", "choices"}
    statement = insert_sql(cols)
    assert "%(seed)s" in statement
    assert "%(workspaces)s" in statement
    assert "random()" not in statement


def test_percentiles_are_nearest_rank_and_empty_is_explicit() -> None:
    assert percentiles([float(i) for i in range(1, 101)])["p99_ms"] == 99
    assert percentiles([2, 1])["p95_ms"] == 2
    assert percentiles([])["p95_ms"] is None
