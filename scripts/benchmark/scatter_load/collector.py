"""Metric collector for the PostgreSQL scatter load test.

Samples PostgreSQL activity at >=1Hz and captures the surrounding evidence a
run needs to be interpretable later: the resolved Compose model, effective
container limits and OOM/restart state, effective PostgreSQL settings, service
logs, fixture row correctness, and the Tracecat commit plus container image
identifiers.

Run it alongside the load runner, pointed at the same artifact directory:

    uv run python -m scripts.benchmark.scatter_load.collector \\
        --run-id scatter-abc123 \\
        --compose-project tracecat-my-worktree-1 \\
        --compose-file docker-compose.dev.yml \\
        --compose-file docker-compose.loadtest.yml \\
        --dsn postgresql://postgres:postgres@localhost:5432/postgres

Stop it with SIGINT; it writes its manifest on the way out.

See scripts/benchmark/postgres-scatter-load-test-plan.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import asyncpg

from .models import (
    CollectorConfig,
    CollectorManifest,
    ContainerState,
    PgActivitySample,
    RowCorrectness,
)

DEFAULT_ARTIFACT_ROOT: Final = "/tmp/tracecat-scatter-load"
DEFAULT_DSN: Final = "postgresql://postgres:postgres@localhost:5432/postgres"
DEFAULT_LOG_SERVICES: Final = ("api", "worker", "executor", "postgres_db")
DEFAULT_TABLE_NAME: Final = "scatter_load_rows"
IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ACTIVITY_SQL: Final = """
SELECT
    count(*)::int AS total,
    count(*) FILTER (WHERE state = 'active')::int AS active,
    count(*) FILTER (WHERE state = 'idle')::int AS idle,
    count(*) FILTER (WHERE state = 'idle in transaction')::int AS idle_in_txn,
    count(*) FILTER (
        WHERE state = 'idle in transaction (aborted)'
    )::int AS idle_in_txn_aborted,
    count(*) FILTER (WHERE wait_event IS NOT NULL)::int AS waiting,
    max(EXTRACT(EPOCH FROM (now() - xact_start)))::float8 AS longest_txn,
    max(EXTRACT(EPOCH FROM (now() - query_start)))::float8 AS longest_query
FROM pg_stat_activity
WHERE backend_type = 'client backend'
"""

WAIT_EVENT_SQL: Final = """
SELECT
    coalesce(wait_event_type, 'none') || ':' || coalesce(wait_event, 'none') AS event,
    count(*)::int AS sessions
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY 1
"""

APPLICATION_NAME_SQL: Final = """
SELECT
    coalesce(nullif(application_name, ''), 'unknown') AS application_name,
    count(*)::int AS sessions
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY 1
"""

DATABASE_COUNTERS_SQL: Final = """
SELECT
    coalesce(sum(xact_commit), 0)::bigint AS xact_commit,
    coalesce(sum(xact_rollback), 0)::bigint AS xact_rollback,
    coalesce(sum(deadlocks), 0)::bigint AS deadlocks
FROM pg_stat_database
WHERE datname IS NOT NULL
"""

SETTINGS_SQL: Final = """
SELECT name, setting, unit, source, boot_val
FROM pg_settings
WHERE name = ANY($1::text[])
"""

FIND_TABLE_SQL: Final = """
SELECT table_schema
FROM information_schema.tables
WHERE table_name = $1 AND table_schema LIKE 'tables\\_%'
ORDER BY table_schema
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_command(args: list[str], *, timeout: float = 120.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout, result.stderr


def _as_int(value: object) -> int:
    """Coerce a driver-typed scalar to int, treating NULL as zero."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(str(value))


def _quote_identifier(value: str) -> str:
    """Validate then quote an SQL identifier.

    Only identifiers matching a strict pattern are accepted, because schema and
    table names have to be interpolated into DDL-shaped SQL that cannot use
    bind parameters.
    """
    if not IDENTIFIER_RE.match(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return f'"{value}"'


class PgSampler:
    """Holds one long-lived sampling connection plus a periodic slot probe."""

    def __init__(self, dsn: str, settings_of_interest: tuple[str, ...]) -> None:
        self._dsn = dsn
        self._settings_of_interest = settings_of_interest
        self._conn: asyncpg.Connection | None = None
        self._prev_commit = 0
        self._prev_rollback = 0
        self._prev_deadlocks = 0
        self._primed = False
        self._connection_slot_errors = 0
        self._max_connections = 0
        self._superuser_reserved = 0

    async def connect(self) -> None:
        conn = await asyncpg.connect(
            self._dsn, server_settings={"application_name": "scatter-load-collector"}
        )
        self._conn = conn
        self._max_connections = _as_int(await conn.fetchval("SHOW max_connections"))
        self._superuser_reserved = _as_int(
            await conn.fetchval("SHOW superuser_reserved_connections")
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def probe_connection_slots(self) -> None:
        """Open and close a throwaway connection to detect slot exhaustion.

        Uses asyncpg's typed `TooManyConnectionsError` (SQLSTATE 53300) rather
        than matching error text. This probe deliberately draws on the
        administrative reserve: if it starts failing, the reserve is gone, which
        is one of the plan's abort conditions.
        """
        try:
            probe = await asyncpg.connect(
                self._dsn,
                server_settings={"application_name": "scatter-load-probe"},
                timeout=5,
            )
        except asyncpg.exceptions.TooManyConnectionsError:
            self._connection_slot_errors += 1
            return
        except (OSError, asyncpg.PostgresError, TimeoutError):
            return
        await probe.close()

    async def sample(self) -> PgActivitySample:
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")

        activity = await conn.fetchrow(ACTIVITY_SQL)
        wait_rows = await conn.fetch(WAIT_EVENT_SQL)
        app_rows = await conn.fetch(APPLICATION_NAME_SQL)
        counters = await conn.fetchrow(DATABASE_COUNTERS_SQL)
        if activity is None or counters is None:
            raise RuntimeError("PostgreSQL returned no activity rows")

        commit = int(counters["xact_commit"])
        rollback = int(counters["xact_rollback"])
        deadlocks = int(counters["deadlocks"])
        if not self._primed:
            self._prev_commit, self._prev_rollback, self._prev_deadlocks = (
                commit,
                rollback,
                deadlocks,
            )
            self._primed = True

        sample = PgActivitySample(
            sampled_at=_utc_now_iso(),
            monotonic=time.monotonic(),
            max_connections=self._max_connections,
            superuser_reserved_connections=self._superuser_reserved,
            total_connections=int(activity["total"]),
            active=int(activity["active"]),
            idle=int(activity["idle"]),
            idle_in_transaction=int(activity["idle_in_txn"]),
            idle_in_transaction_aborted=int(activity["idle_in_txn_aborted"]),
            waiting=int(activity["waiting"]),
            wait_events={str(r["event"]): int(r["sessions"]) for r in wait_rows},
            application_names={
                str(r["application_name"]): int(r["sessions"]) for r in app_rows
            },
            longest_transaction_seconds=(
                float(activity["longest_txn"])
                if activity["longest_txn"] is not None
                else None
            ),
            longest_query_seconds=(
                float(activity["longest_query"])
                if activity["longest_query"] is not None
                else None
            ),
            xact_commit_delta=commit - self._prev_commit,
            xact_rollback_delta=rollback - self._prev_rollback,
            deadlocks_delta=deadlocks - self._prev_deadlocks,
            connection_slot_errors=self._connection_slot_errors,
        )
        self._prev_commit, self._prev_rollback, self._prev_deadlocks = (
            commit,
            rollback,
            deadlocks,
        )
        return sample

    async def effective_settings(self) -> list[dict[str, str | None]]:
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")
        rows = await conn.fetch(SETTINGS_SQL, list(self._settings_of_interest))
        return [
            {
                "name": str(row["name"]),
                "setting": str(row["setting"]),
                "unit": str(row["unit"]) if row["unit"] is not None else None,
                "source": str(row["source"]),
                "boot_val": str(row["boot_val"])
                if row["boot_val"] is not None
                else None,
            }
            for row in rows
        ]

    async def row_correctness(
        self, table_name: str, run_id: str | None
    ) -> RowCorrectness | None:
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")
        schema_name = await conn.fetchval(FIND_TABLE_SQL, table_name)
        if schema_name is None:
            return None

        qualified = (
            f"{_quote_identifier(str(schema_name))}.{_quote_identifier(table_name)}"
        )
        totals = await conn.fetchrow(
            f"SELECT count(*)::bigint AS total, "  # noqa: S608 - identifiers validated
            f"count(DISTINCT dedupe_key)::bigint AS distinct_keys FROM {qualified}"
        )
        per_run = await conn.fetchrow(
            f"SELECT count(*)::bigint AS total, "  # noqa: S608 - identifiers validated
            f"count(DISTINCT dedupe_key)::bigint AS distinct_keys "
            f"FROM {qualified} WHERE run_id = $1",
            run_id or "",
        )
        if totals is None or per_run is None:
            return None

        total_rows = int(totals["total"])
        distinct_keys = int(totals["distinct_keys"])
        return RowCorrectness(
            schema_name=str(schema_name),
            table_name=table_name,
            total_rows=total_rows,
            distinct_dedupe_keys=distinct_keys,
            duplicate_dedupe_keys=total_rows - distinct_keys,
            rows_for_run=int(per_run["total"]),
            distinct_dedupe_keys_for_run=int(per_run["distinct_keys"]),
        )


def capture_compose_config(config: CollectorConfig, artifact_dir: Path) -> str | None:
    """Write the fully resolved Compose model for the cluster under test."""
    args = ["docker", "compose", "-p", config.compose_project]
    for compose_file in config.compose_files:
        args += ["-f", compose_file]
    args.append("config")
    code, stdout, stderr = _run_command(args)
    target = artifact_dir / "compose_config.yml"
    target.write_text(
        stdout if code == 0 else f"# failed: {stderr}\n", encoding="utf-8"
    )
    return str(target)


def capture_container_state(config: CollectorConfig, artifact_dir: Path) -> str | None:
    """Record effective limits plus restart/OOM state and image identifiers."""
    code, stdout, _ = _run_command(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={config.compose_project}",
            "--format",
            "{{.ID}}",
        ]
    )
    states: list[ContainerState] = []
    if code == 0:
        for container_id in [line for line in stdout.splitlines() if line.strip()]:
            inspect_code, inspect_out, _ = _run_command(
                ["docker", "inspect", container_id]
            )
            if inspect_code != 0:
                continue
            parsed = json.loads(inspect_out)
            if not isinstance(parsed, list) or not parsed:
                continue
            info = parsed[0]
            state = info.get("State", {})
            host_config = info.get("HostConfig", {})
            labels = info.get("Config", {}).get("Labels", {})
            states.append(
                ContainerState(
                    name=str(info.get("Name", "")).lstrip("/"),
                    service=str(labels.get("com.docker.compose.service", "")),
                    image=str(info.get("Config", {}).get("Image", "")),
                    image_id=str(info.get("Image", "")),
                    status=str(state.get("Status", "")),
                    restart_count=int(info.get("RestartCount", 0)),
                    oom_killed=bool(state.get("OOMKilled", False)),
                    exit_code=state.get("ExitCode"),
                    nano_cpus=host_config.get("NanoCpus"),
                    memory_limit_bytes=host_config.get("Memory"),
                    memory_swap_limit_bytes=host_config.get("MemorySwap"),
                    pids_limit=host_config.get("PidsLimit"),
                )
            )
    target = artifact_dir / "containers.json"
    target.write_text(json.dumps(states, indent=2, default=str), encoding="utf-8")
    return str(target)


def capture_service_logs(config: CollectorConfig, artifact_dir: Path) -> dict[str, str]:
    """Dump the tail of each service log."""
    log_dir = artifact_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for service in config.log_services:
        args = ["docker", "compose", "-p", config.compose_project]
        for compose_file in config.compose_files:
            args += ["-f", compose_file]
        args += ["logs", "--no-color", "--tail", str(config.log_tail), service]
        code, stdout, stderr = _run_command(args)
        target = log_dir / f"{service}.log"
        target.write_text(stdout if code == 0 else stderr, encoding="utf-8")
        written[service] = str(target)
    return written


def capture_commit(repo_root: Path) -> tuple[str, bool]:
    code, stdout, _ = _run_command(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
    commit = stdout.strip() if code == 0 else "unknown"
    status_code, status_out, _ = _run_command(
        ["git", "-C", str(repo_root), "status", "--porcelain"]
    )
    dirty = bool(status_out.strip()) if status_code == 0 else False
    return commit, dirty


class MetricCollector:
    """Owns the sampling loop and the one-shot environment captures."""

    def __init__(self, config: CollectorConfig) -> None:
        self._config = config
        self._artifact_dir = Path(config.artifact_dir)
        self._sampler = PgSampler(config.dsn, config.settings_of_interest)
        self._stop = asyncio.Event()
        self._sample_count = 0

    def request_stop(self) -> None:
        self._stop.set()

    async def _sample_loop(self, sink: Path) -> None:
        probe_every = max(1, int(round(5.0 / self._config.sample_interval_seconds)))
        with sink.open("a", encoding="utf-8") as handle:
            deadline = (
                time.monotonic() + self._config.duration_seconds
                if self._config.duration_seconds is not None
                else None
            )
            while not self._stop.is_set():
                tick = time.monotonic()
                try:
                    sample = await self._sampler.sample()
                except (asyncpg.PostgresError, OSError, RuntimeError) as exc:
                    # Losing observability of the database is an abort condition.
                    handle.write(
                        json.dumps(
                            {
                                "sampled_at": _utc_now_iso(),
                                "observability_failure": type(exc).__name__,
                            }
                        )
                        + "\n"
                    )
                    handle.flush()
                    return
                handle.write(json.dumps(sample) + "\n")
                handle.flush()
                self._sample_count += 1

                if self._sample_count % probe_every == 0:
                    await self._sampler.probe_connection_slots()

                if deadline is not None and time.monotonic() >= deadline:
                    return
                delay = self._config.sample_interval_seconds - (time.monotonic() - tick)
                if delay > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def run(self, repo_root: Path) -> CollectorManifest:
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        started_at = _utc_now_iso()

        artifacts: dict[str, str] = {}
        compose_config = capture_compose_config(self._config, self._artifact_dir)
        if compose_config:
            artifacts["compose_config"] = compose_config

        await self._sampler.connect()
        try:
            settings = await self._sampler.effective_settings()
            settings_path = self._artifact_dir / "pg_settings.json"
            settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            artifacts["pg_settings"] = str(settings_path)

            samples_path = self._artifact_dir / "pg_activity.jsonl"
            artifacts["pg_activity"] = str(samples_path)
            await self._sample_loop(samples_path)

            correctness = await self._sampler.row_correctness(
                self._config.table_name, self._config.run_id
            )
            if correctness is not None:
                correctness_path = self._artifact_dir / "row_correctness.json"
                correctness_path.write_text(
                    json.dumps(correctness, indent=2), encoding="utf-8"
                )
                artifacts["row_correctness"] = str(correctness_path)
        finally:
            await self._sampler.close()

        containers = capture_container_state(self._config, self._artifact_dir)
        if containers:
            artifacts["containers"] = containers
        for service, path in capture_service_logs(
            self._config, self._artifact_dir
        ).items():
            artifacts[f"log:{service}"] = path

        # The runner writes these into the same directory by convention.
        for name in ("scenario.json", "runner_results.jsonl", "summary.txt"):
            candidate = self._artifact_dir / name
            if candidate.exists():
                artifacts[name] = str(candidate)

        commit, dirty = capture_commit(repo_root)
        manifest = CollectorManifest(
            run_id=self._config.run_id,
            artifact_dir=str(self._artifact_dir),
            started_at=started_at,
            finished_at=_utc_now_iso(),
            sample_interval_seconds=self._config.sample_interval_seconds,
            sample_count=self._sample_count,
            tracecat_commit=commit,
            tracecat_commit_dirty=dirty,
            compose_project=self._config.compose_project,
            artifacts=artifacts,
        )
        (self._artifact_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scatter_load.collector",
        description="PostgreSQL activity and environment collector for the scatter load test.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact-root", default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument(
        "--sample-interval-seconds",
        type=float,
        default=0.5,
        help="Must be <= 1.0 to satisfy the plan's >=1Hz requirement.",
    )
    parser.add_argument("--compose-project", required=True)
    parser.add_argument(
        "--compose-file",
        action="append",
        default=None,
        help="Repeatable. Should match the files `scripts/cluster` resolved.",
    )
    parser.add_argument(
        "--log-service", action="append", default=None, help="Repeatable."
    )
    parser.add_argument("--log-tail", type=int, default=5000)
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="Stop sampling after this long. Omit to sample until SIGINT.",
    )
    return parser


async def amain(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]

    if args.sample_interval_seconds > 1.0:
        print(
            "--sample-interval-seconds must be <= 1.0 (>=1Hz)",
            file=sys.stderr,
        )
        return 2

    config = CollectorConfig(
        run_id=args.run_id,
        artifact_dir=str(Path(args.artifact_root) / args.run_id),
        dsn=args.dsn,
        sample_interval_seconds=args.sample_interval_seconds,
        compose_project=args.compose_project,
        compose_files=tuple(args.compose_file or ("docker-compose.dev.yml",)),
        log_services=tuple(args.log_service or DEFAULT_LOG_SERVICES),
        log_tail=args.log_tail,
        table_name=args.table_name,
        duration_seconds=args.duration_seconds,
    )

    collector = MetricCollector(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, collector.request_stop)

    manifest = await collector.run(repo_root)
    print(f"samples: {manifest['sample_count']}")
    print(f"artifacts: {manifest['artifact_dir']}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
