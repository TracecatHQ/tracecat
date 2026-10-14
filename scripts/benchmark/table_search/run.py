"""Run with python -m scripts.benchmark.table_search.run; see README.md."""

import argparse
import os
import platform
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import orjson
import psycopg
from psycopg import Connection, Error, sql
from pydantic import JsonValue

from .queries import SEARCHES, Search, query
from .safety import check_contract, safety_probes
from .schema import (
    Column,
    build_indexes,
    columns,
    create_source,
    drop_indexes,
    insert_sql,
    populate_projection,
)


@dataclass(frozen=True, slots=True)
class Settings:
    sizes: tuple[int, ...]
    width: int
    workspaces: int
    seed: int
    samples: int
    broad_samples: int
    write_batches: int
    write_batch_size: int
    timeout_ms: int
    max_value_bytes: int
    environment: str


def percentiles(values: list[float]) -> dict[str, JsonValue]:
    """Use nearest-rank quantiles; retain raw samples to reveal small-N limits."""
    ordered = sorted(values)
    if not ordered:
        return {"samples": [], "p50_ms": None, "p95_ms": None, "p99_ms": None}
    return {
        "samples": values,
        **{
            f"p{p}_ms": ordered[max(0, (len(ordered) * p + 99) // 100 - 1)]
            for p in (50, 95, 99)
        },
    }


def connect(dsn: str, schema: str, timeout_ms: int) -> Connection:
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute(
        sql.SQL("SET search_path TO {}, public, pg_catalog").format(
            sql.Identifier(schema)
        )
    )
    conn.execute(
        "SELECT set_config('statement_timeout', %s, false)", (str(timeout_ms),)
    )
    return conn


def validate_database(conn: Connection) -> None:
    """Refuse application databases even if accidentally renamed with the prefix."""
    name = conn.execute("SELECT current_database()").fetchone()[0]
    if not name.startswith("tracecat_search_bench_"):
        raise ValueError("Database name must start with tracecat_search_bench_")
    count = conn.execute("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p','v','m','f','S')
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
    """).fetchone()[0]
    if count:
        raise ValueError("Benchmark database must contain no user relations")


def environment(conn: Connection, settings: Settings) -> dict[str, JsonValue]:
    """Record engine/session settings without recording DSNs or credentials."""
    names = (
        "server_version",
        "server_encoding",
        "shared_buffers",
        "work_mem",
        "maintenance_work_mem",
        "effective_cache_size",
        "max_parallel_workers_per_gather",
        "random_page_cost",
        "jit",
        "synchronous_commit",
        "wal_level",
        "default_text_search_config",
    )
    return {
        "declared_environment": settings.environment,
        "client_machine": platform.machine(),
        "client_cpu_count": os.cpu_count(),
        "engine": conn.execute("SELECT version()").fetchone()[0],
        "settings": {
            name: conn.execute(
                sql.SQL("SHOW {}").format(sql.Identifier(name))
            ).fetchone()[0]
            for name in names
        },
        "pg_trgm": conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname='pg_trgm'"
        ).fetchone()[0],
        "role_is_superuser": conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0],
        "locale": list(
            conn.execute(
                "SELECT datcollate,datctype,datlocprovider FROM pg_database WHERE datname=current_database()"
            ).fetchone()
        ),
        "cache_policy": "one untimed warmup; no cache flush; client wall time includes transferring 50 full rows",
        "percentile_method": "nearest rank, completed queries only; timeouts reported separately",
    }


def measure(
    conn: Connection,
    cols: tuple[Column, ...],
    search: Search,
    samples: int,
    *,
    projection: bool,
) -> dict[str, JsonValue]:
    statement, params = query(cols, search, projection=projection)
    result: dict[str, JsonValue] = {
        "name": search.name,
        "sql": statement,
        "params": list(params),
    }
    try:
        first = conn.execute(statement, params).fetchall()
        result["returned_rows"] = len(first)
        plan = conn.execute(
            "EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) " + statement,
            params,
        ).fetchone()[0]
        result["plan"] = plan
    except psycopg.errors.QueryCanceled:
        result["warmup_or_explain_timeout"] = True
        result["timeouts"] = 1
        result["skipped_samples"] = samples
        result.update(percentiles([]))
        return result
    elapsed = []
    timeouts = 0
    for _ in range(samples):
        start = time.perf_counter()
        try:
            conn.execute(statement, params).fetchall()
        except psycopg.errors.QueryCanceled:
            timeouts += 1
        else:
            elapsed.append((time.perf_counter() - start) * 1000)
    result.update(percentiles(elapsed))
    result["timeouts"] = timeouts
    # The cursor uses the last row's exact score/ID. Verify stable pages and
    # deduplication before recording the separately measured second page.
    if first:
        page_sql, page_params = query(
            cols, search, projection=projection, cursor=(first[-1][0], first[-1][1])
        )
        start = time.perf_counter()
        try:
            second = conn.execute(page_sql, page_params).fetchall()
        except psycopg.errors.QueryCanceled:
            result["page_two_timeout"] = True
        else:
            if {r[1] for r in first} & {r[1] for r in second}:
                raise AssertionError("Keyset pages overlap")
            result["page_two_ms"] = (time.perf_counter() - start) * 1000
            result["page_two_rows"] = len(second)
    return result


def relation_sizes(conn: Connection) -> list[dict[str, JsonValue]]:
    return [
        {"name": name, "kind": kind, "bytes": size, "total_bytes": total}
        for name, kind, size, total in conn.execute("""
            SELECT c.relname,c.relkind,pg_relation_size(c.oid),pg_total_relation_size(c.oid) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relkind IN ('r','i') ORDER BY c.relname
        """)
    ]


def writes(
    dsn: str,
    schema: str,
    cols: tuple[Column, ...],
    settings: Settings,
    size: int,
    *,
    projection: bool,
    started: threading.Event | None = None,
    release: threading.Event | None = None,
    finished: threading.Event | None = None,
) -> dict[str, JsonValue]:
    """Insert then update real indexed rows; roll back only the task's probe rows."""
    timings = []
    updates = []
    workspaces = list(range(1, settings.workspaces + 1))
    with connect(dsn, schema, settings.timeout_ms) as writer:
        batch = 0
        while batch < settings.write_batches or (
            finished is not None and not finished.is_set()
        ):
            start_id = size + batch * settings.write_batch_size + 1
            end_id = start_id + settings.write_batch_size - 1
            start = time.perf_counter()
            writer.execute(
                insert_sql(cols),
                {
                    "start": start_id,
                    "end": end_id,
                    "seed": settings.seed,
                    "workspaces": settings.workspaces,
                },
            )
            if projection:
                populate_projection(writer, cols, after=start_id - 1)
            timings.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            writer.execute(
                "UPDATE source SET text_0=text_0 || ' updated', revision=revision+1 WHERE id BETWEEN %s AND %s",
                (start_id, end_id),
            )
            if projection:
                writer.execute(
                    "DELETE FROM projection WHERE workspace = ANY(%s) AND row_id >= %s",
                    (workspaces, start_id),
                )
                writer.execute(
                    "DELETE FROM literal_projection WHERE workspace = ANY(%s) AND row_id >= %s",
                    (workspaces, start_id),
                )
                populate_projection(writer, cols, after=start_id - 1)
            updates.append((time.perf_counter() - start) * 1000)
            if started and release and batch == 0:
                started.set()
                release.wait(30)
            writer.execute(
                "DELETE FROM projection WHERE workspace = ANY(%s) AND row_id >= %s",
                (workspaces, start_id),
            )
            writer.execute(
                "DELETE FROM literal_projection WHERE workspace = ANY(%s) AND row_id >= %s",
                (workspaces, start_id),
            )
            writer.execute("DELETE FROM source WHERE id >= %s", (start_id,))
            batch += 1
    total_s = sum(timings) / 1000
    return {
        "insert_batch": percentiles(timings),
        "update_batch": percentiles(updates),
        "insert_rows_per_second": batch * settings.write_batch_size / total_s,
        "rows": batch * settings.write_batch_size,
        "projection_ownership": "synchronous client prototype; production requires database change capture and fenced async publication",
    }


def timed(action) -> float:
    start = time.perf_counter()
    action()
    return time.perf_counter() - start


def run_scenario(
    conn: Connection, dsn: str, schema: str, settings: Settings, size: int
) -> dict[str, JsonValue]:
    cols = columns(settings.width)
    create_source(conn, cols)
    contract = check_contract(conn)
    # Build/import statements get a larger limit than user-facing searches.
    conn.execute("SET statement_timeout = 0")
    load_s = timed(
        lambda: conn.execute(
            insert_sql(cols),
            {
                "start": 1,
                "end": size,
                "seed": settings.seed,
                "workspaces": settings.workspaces,
            },
        )
    )
    conn.execute("ANALYZE source")
    result: dict[str, JsonValue] = {
        "rows": size,
        "width": settings.width,
        "contract": contract,
        "import_seconds": load_s,
        "import_rows_per_second": size / load_s,
    }
    stages: list[dict[str, JsonValue]] = []
    baseline = writes(dsn, schema, cols, settings, size, projection=False)
    result["unindexed_writes"] = baseline
    for mode in ("fts", "trgm", "chunked"):
        print(f"rows={size} stage={mode}: building", flush=True)
        conn.execute("SET statement_timeout = 0")
        if mode == "chunked":
            build_s = timed(lambda: populate_projection(conn, cols))
            index_s = timed(
                lambda: conn.execute(
                    "CREATE INDEX projection_fts ON projection USING gin(vector); CREATE INDEX projection_trgm ON literal_projection USING gin(body public.gin_trgm_ops)"
                )
            )
            conn.execute("ANALYZE projection; ANALYZE literal_projection")
        else:
            build_s = timed(lambda mode=mode: build_indexes(conn, cols, mode))
            index_s = build_s
        conn.execute("ANALYZE source")
        conn.execute(
            "SELECT set_config('statement_timeout', %s, false)",
            (str(settings.timeout_ms),),
        )
        searches = [s for s in SEARCHES if mode == "chunked" or s.mode == mode]
        measurements = [
            measure(
                conn,
                cols,
                s,
                settings.broad_samples
                if "broad" in s.name
                or "no_trigrams" in s.name
                or s.name in ("json_key", "literal_json_serialized")
                else settings.samples,
                projection=mode == "chunked",
            )
            for s in searches
        ]
        write_result = writes(
            dsn, schema, cols, settings, size, projection=mode == "chunked"
        )
        # Writers must signal after their first committed batch. They then start
        # the next batch as the reader begins, avoiding a no-overlap measurement.
        started, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                writes,
                dsn,
                schema,
                cols,
                settings,
                size,
                projection=mode == "chunked",
                started=started,
                release=release,
                finished=finished,
            )
            if not started.wait(30):
                release.set()
                finished.set()
                future.result()
                raise RuntimeError("Writer did not start")
            release.set()
            try:
                concurrent = measure(
                    conn,
                    cols,
                    searches[0],
                    settings.samples,
                    projection=mode == "chunked",
                )
            finally:
                finished.set()
            concurrent_writes = future.result()
        stages.append(
            {
                "mode": mode,
                "build_seconds": build_s,
                "index_build_seconds": index_s,
                "relations": relation_sizes(conn),
                "queries": measurements,
                "writes": write_result,
                "concurrent_search": concurrent,
                "concurrent_writes": concurrent_writes,
                "write_throughput_ratio_to_unindexed": write_result[
                    "insert_rows_per_second"
                ]
                / baseline["insert_rows_per_second"],
            }
        )
        print(f"rows={size} stage={mode}: measured", flush=True)
        if mode != "chunked":
            drop_indexes(conn, cols, mode)
    result["stages"] = stages
    conn.execute(
        "DROP TABLE source, projection, literal_projection; DROP FUNCTION json_values(jsonb), value_chunks(text)"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=[10_000, 100_000, 1_000_000]
    )
    parser.add_argument("--width", type=int, default=4)
    parser.add_argument("--workspaces", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2022)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--broad-samples", type=int, default=10)
    parser.add_argument("--write-batches", type=int, default=10)
    parser.add_argument("--write-batch-size", type=int, default=100)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--max-value-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument(
        "--environment",
        required=True,
        help="Declare server CPU/RAM/storage and contention",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(
        tuple(args.sizes),
        args.width,
        args.workspaces,
        args.seed,
        args.samples,
        args.broad_samples,
        args.write_batches,
        args.write_batch_size,
        args.timeout_ms,
        args.max_value_bytes,
        args.environment,
    )
    if (
        any(n <= 0 for n in settings.sizes)
        or min(
            settings.samples,
            settings.broad_samples,
            settings.write_batch_size,
            settings.timeout_ms,
        )
        < 1
        or settings.write_batches < 2
        or settings.workspaces < 2
        or settings.max_value_bytes < 2 * 1024 * 1024
    ):
        parser.error(
            "Require positive sizes/samples, at least two writer batches/workspaces and >=2 MiB safety values"
        )
    columns(settings.width)
    dsn = os.environ["TABLE_SEARCH_BENCH_DSN"]
    schema = f"tsbench_{uuid.uuid4().hex}"
    report: dict[str, JsonValue] = {
        "started_at": datetime.now(UTC).isoformat(),
        "settings": asdict(settings),
        "scenarios": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        validate_database(conn)
        # Prevent two runners from passing the empty-database check together.
        if not conn.execute("SELECT pg_try_advisory_lock(2022, 2022)").fetchone()[0]:
            raise ValueError("Another benchmark owns this database")
        validate_database(conn)
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute(
                sql.SQL("SET search_path TO {}, public, pg_catalog").format(
                    sql.Identifier(schema)
                )
            )
            report["environment"] = environment(conn, settings)
            # Safety helpers are measured once independently of ordinary rows.
            create_source(conn, columns(4))
            report["safety"] = safety_probes(conn, settings.max_value_bytes)
            conn.execute(
                "DROP TABLE source, projection, literal_projection; DROP FUNCTION json_values(jsonb), value_chunks(text)"
            )
            scenarios: list[JsonValue] = []
            report["scenarios"] = scenarios
            for size in settings.sizes:
                scenarios.append(run_scenario(conn, dsn, schema, settings, size))
                args.output.write_bytes(
                    orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n"
                )
            report["completed_at"] = datetime.now(UTC).isoformat()
        except Error as error:
            report["failure_sqlstate"] = error.sqlstate
            raise
        finally:
            # Only the unpredictable schema created by this invocation is removed.
            # No databases, extensions, roles, containers or volumes are deleted.
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
            args.output.write_bytes(
                orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n"
            )


if __name__ == "__main__":
    main()
