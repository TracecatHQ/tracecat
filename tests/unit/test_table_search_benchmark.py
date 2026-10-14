"""Benchmark invariants that protect setup and the declared query contract."""

from concurrent.futures import Future
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from scripts.benchmark.table_search.queries import SEARCHES, Search, query
from scripts.benchmark.table_search.run import (
    Settings,
    WriterControl,
    concurrent_writer,
    connect,
    ensure_trigram_extension,
    percentiles,
    run_scenario,
    validate_database,
)
from scripts.benchmark.table_search.safety import plan_uses_index
from scripts.benchmark.table_search.schema import columns, insert_sql
from tracecat.query.lexical_types import ColumnSearchOrder, SearchMode


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


def test_setup_connection_disables_inherited_statement_timeout() -> None:
    conn = Mock()
    with patch("scripts.benchmark.table_search.run.psycopg.connect") as open_connection:
        open_connection.return_value = conn
        assert connect("synthetic", "synthetic", 0) is conn
    open_connection.assert_called_once_with(
        "synthetic", autocommit=True, connect_timeout=10
    )
    conn.execute.assert_any_call(
        "SELECT set_config('statement_timeout', %s, false)", ("0",)
    )


@pytest.mark.parametrize("namespace", ["public", "other_extension_schema"])
def test_trigram_extension_requires_public_namespace(namespace: str) -> None:
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = (namespace,)
    if namespace == "public":
        ensure_trigram_extension(conn)
    else:
        with pytest.raises(ValueError, match="pg_trgm must be installed in public"):
            ensure_trigram_extension(conn)
    assert conn.execute.call_count == 2


def test_next_scenario_clears_search_timeout_before_setup() -> None:
    conn = Mock()
    settings = Settings((100,), 4, 4, 2022, 2, 1, 2, 10, 5000, 2097152, "synthetic")

    def check_setup_timeout(*_args) -> None:
        conn.execute.assert_called_once_with("SET statement_timeout = 0")
        raise RuntimeError("stop before workload setup")

    with patch(
        "scripts.benchmark.table_search.run.create_source",
        side_effect=check_setup_timeout,
    ):
        with pytest.raises(RuntimeError, match="stop before workload setup"):
            run_scenario(conn, "synthetic", "synthetic", settings, 100)


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
        columns(4),
        Search("literal", value, SearchMode.LITERAL, order=ColumnSearchOrder("choice")),
        projection=projection,
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


@pytest.mark.parametrize("projection", [False, True])
@pytest.mark.parametrize("direction,operator", [("asc", ">"), ("desc", "<")])
def test_literal_column_order_and_cursor(
    projection: bool, direction, operator: str
) -> None:
    search = Search(
        "literal",
        "common",
        SearchMode.LITERAL,
        order=ColumnSearchOrder("choice", direction),
    )
    statement, params = query(
        columns(4), search, projection=projection, cursor=("normal", 123)
    )
    assert (
        f"order_value {direction.upper()} NULLS LAST, id {direction.upper()}"
        in statement
    )
    assert f"(order_value, id) {operator} (%s, %s) OR order_value IS NULL" in statement
    assert params[-2:] == ("normal", 123)
    null_sql, null_params = query(
        columns(4), search, projection=projection, cursor=(None, 123)
    )
    assert f"order_value IS NULL AND id {operator} %s" in null_sql
    assert null_params[-1] == 123


def test_literal_rejects_relevance_and_unknown_order_columns() -> None:
    with pytest.raises(ValueError, match="column ordering"):
        Search("literal", "common", SearchMode.LITERAL)
    with pytest.raises(ValueError, match="Ordering column"):
        query(
            columns(4),
            Search(
                "literal",
                "common",
                SearchMode.LITERAL,
                order=ColumnSearchOrder("missing"),
            ),
        )


def test_workload_policy_does_not_depend_on_names_or_case_order() -> None:
    cases = tuple(replace(s, name="renamed") for s in reversed(SEARCHES))
    for stage in ("fts", "trgm", "chunked"):
        matches = [s for s in cases if stage in s.concurrent_for]
        assert len(matches) == 1
        assert matches[0].sampling == "selective"
    assert sorted(s.sampling for s in cases) == sorted(s.sampling for s in SEARCHES)


@pytest.mark.parametrize("interrupt_during_startup", [False, True])
def test_concurrent_writer_cancellation_always_releases_and_stops(
    interrupt_during_startup: bool,
) -> None:
    control = WriterControl()
    settings = Settings((100,), 4, 4, 2022, 2, 1, 2, 10, 5000, 2097152, "synthetic")
    future = Future()
    pool = Mock()
    pool.submit.return_value = future
    wait = (
        Mock(side_effect=KeyboardInterrupt)
        if interrupt_during_startup
        else Mock(return_value=True)
    )
    with (
        patch("scripts.benchmark.table_search.run.WriterControl", return_value=control),
        patch("scripts.benchmark.table_search.run.ThreadPoolExecutor") as executor,
        patch.object(control.ready, "wait", wait),
    ):
        executor.return_value.__enter__.return_value = pool
        with (
            pytest.raises(KeyboardInterrupt),
            concurrent_writer(
                "synthetic", "synthetic", columns(4), settings, 100, projection=False
            ),
        ):
            raise KeyboardInterrupt
    assert control.release.is_set()
    assert control.finished.is_set()


def test_concurrent_writer_propagates_startup_failure() -> None:
    control = WriterControl()
    settings = Settings((100,), 4, 4, 2022, 2, 1, 2, 10, 5000, 2097152, "synthetic")
    future = Future()
    future.set_exception(ValueError("synthetic writer failed"))
    with (
        patch("scripts.benchmark.table_search.run.WriterControl", return_value=control),
        patch("scripts.benchmark.table_search.run.ThreadPoolExecutor") as executor,
        patch.object(control.ready, "wait", return_value=False),
    ):
        executor.return_value.__enter__.return_value.submit.return_value = future
        with (
            pytest.raises(ValueError, match="synthetic writer failed"),
            concurrent_writer(
                "synthetic", "synthetic", columns(4), settings, 100, projection=False
            ),
        ):
            pytest.fail("Failed writer must not start a measurement")
    assert control.release.is_set()
    assert control.finished.is_set()


def test_plan_verification_requires_the_named_index_in_nested_plan() -> None:
    assert plan_uses_index(
        [{"Plan": {"Plans": [{"Index Name": "safety_trgm"}]}}], "safety_trgm"
    )
    assert not plan_uses_index([{"Plan": {"Node Type": "Seq Scan"}}], "safety_trgm")
