"""Benchmark isolated PostgreSQL lexical-search storage prototypes.

Create an empty PostgreSQL 16 database named tracecat_search_bench_* and set
TABLE_SEARCH_BENCH_DSN to its connection URI. The role needs CONNECT and CREATE;
pg_trgm must be available. Setup refuses existing user relations. Cleanup removes
only this invocation's random schema, preserving the database and extension.

Run from the repository root with synced dependencies::

    uv run python -m scripts.benchmark.table_search.run \\
      --sizes 10000 100000 1000000 --width 4 --workspaces 4 --seed 2022 \\
      --environment 'Server CPU, RAM, storage and competing workloads' \\
      --output /tmp/table-search-scale.json

Use --width 16 for a wide-schema run and --max-value-bytes 20971520 for 20 MiB
safety probes. These are experimental chunkers; production requirements and the
recorded storage decision live in the ENG-2022 pull-request description.
"""

import argparse
import hashlib
import os
import platform
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import orjson
import psycopg
from psycopg import Connection, Error, sql
from pydantic import JsonValue

from tracecat.query.lexical_types import ColumnSearchOrder, SearchMode

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
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=10)
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
        "cache_policy": "one untimed query warmup plus one EXPLAIN ANALYZE execution before sampling; no cache flush; client wall time includes transferring 50 full rows",
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
        "mode": search.mode.value,
        "order": asdict(search.order)
        if isinstance(search.order, ColumnSearchOrder)
        else search.order,
        "sampling": search.sampling,
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
            cols, search, projection=projection, cursor=(first[-1][1], first[-1][2])
        )
        start = time.perf_counter()
        try:
            second = conn.execute(page_sql, page_params).fetchall()
        except psycopg.errors.QueryCanceled:
            result["page_two_timeout"] = True
        else:
            if {r[2] for r in first} & {r[2] for r in second}:
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


@dataclass(frozen=True, slots=True)
class WriterControl:
    ready: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)


@contextmanager
def concurrent_writer(
    dsn: str,
    schema: str,
    cols: tuple[Column, ...],
    settings: Settings,
    size: int,
    *,
    projection: bool,
) -> Iterator[Future[dict[str, JsonValue]]]:
    """Own writer startup and always stop/release it before joining the thread."""
    control = WriterControl()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            future = pool.submit(
                writes,
                dsn,
                schema,
                cols,
                settings,
                size,
                projection=projection,
                control=control,
            )
            deadline = time.monotonic() + 30
            while not control.ready.wait(0.05):
                if future.done():
                    future.result()
                    raise RuntimeError("Writer stopped before its first batch")
                if time.monotonic() >= deadline:
                    raise RuntimeError("Writer did not start")
            control.release.set()
            yield future
        finally:
            control.finished.set()
            control.release.set()


def writes(
    dsn: str,
    schema: str,
    cols: tuple[Column, ...],
    settings: Settings,
    size: int,
    *,
    projection: bool,
    control: WriterControl | None = None,
) -> dict[str, JsonValue]:
    """Measure atomic insert/update batches, then delete the task's probe rows."""
    timings = []
    updates = []
    workspaces = list(range(1, settings.workspaces + 1))
    with connect(dsn, schema, settings.timeout_ms) as writer:
        batch = 0
        while batch < settings.write_batches or control is not None:
            if control is not None and control.finished.is_set():
                break
            start_id = size + batch * settings.write_batch_size + 1
            end_id = start_id + settings.write_batch_size - 1
            start = time.perf_counter()
            with writer.transaction():
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
            with writer.transaction():
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
            if control is not None and batch == 0:
                control.ready.set()
                control.release.wait(30)
            with writer.transaction():
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
        "insert_rows_per_second": batch * settings.write_batch_size / total_s
        if total_s
        else 0,
        "rows": batch * settings.write_batch_size,
        "projection_ownership": "synchronous client prototype; production requires database change capture and fenced async publication",
    }


def timed(action) -> float:
    start = time.perf_counter()
    action()
    return time.perf_counter() - start


def load_source(
    conn: Connection, cols: tuple[Column, ...], settings: Settings, size: int
) -> float:
    """Reset heaps/indexes so each prototype starts with the same seeded corpus."""
    conn.execute("SET statement_timeout = 0")
    conn.execute("TRUNCATE source, projection, literal_projection")
    elapsed = timed(
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
    return elapsed


def run_scenario(
    conn: Connection, dsn: str, schema: str, settings: Settings, size: int
) -> dict[str, JsonValue]:
    cols = columns(settings.width)
    create_source(conn, cols)
    contract = check_contract(conn)
    load_s = load_source(conn, cols, settings, size)
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
        stage_import_s = load_source(conn, cols, settings, size)
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
        search_mode = SearchMode.FULL_TEXT if mode == "fts" else SearchMode.LITERAL
        searches = [s for s in SEARCHES if mode == "chunked" or s.mode == search_mode]
        concurrent_cases = [s for s in searches if mode in s.concurrent_for]
        if len(concurrent_cases) != 1:
            raise ValueError("Each prototype requires exactly one concurrent workload")
        clean_relations = relation_sizes(conn)
        measurements = [
            measure(
                conn,
                cols,
                s,
                settings.broad_samples if s.sampling == "broad" else settings.samples,
                projection=mode == "chunked",
            )
            for s in searches
        ]
        write_result = writes(
            dsn, schema, cols, settings, size, projection=mode == "chunked"
        )
        with concurrent_writer(
            dsn, schema, cols, settings, size, projection=mode == "chunked"
        ) as future:
            concurrent = measure(
                conn,
                cols,
                concurrent_cases[0],
                settings.samples,
                projection=mode == "chunked",
            )
        concurrent_writes = future.result()
        stages.append(
            {
                "mode": mode,
                "source_import_seconds": stage_import_s,
                "build_seconds": build_s,
                "index_build_seconds": index_s,
                "relations": clean_relations,
                "post_write_relations": relation_sizes(conn),
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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
        "protocol_version": 2,
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))
        },
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
