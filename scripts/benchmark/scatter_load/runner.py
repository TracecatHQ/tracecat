"""Asynchronous API load runner for the PostgreSQL scatter load test.

Starts workflows through the public admission path
(`POST /workspaces/{workspace_id}/workflow-executions`) and polls them to a
terminal state at a fixed interval. It never calls Temporal directly and never
deletes fixtures.

Usage (from the repository root):

    uv run python -m scripts.benchmark.scatter_load.runner \\
        --base-url http://localhost:80/api \\
        --workflow-count 8 --branch-count 64 \\
        --ramp-seconds 30 --steady-state-seconds 120

See scripts/benchmark/postgres-scatter-load-test-plan.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

from .client import ApiError, TracecatClient
from .fixtures import ensure_fixtures, resolve_workspace
from .models import (
    TERMINAL_STATUSES,
    AuthConfig,
    ExecutionRecord,
    FailureMode,
    LatencySummary,
    Phase,
    RunSummary,
    ScenarioConfig,
    WritePath,
)

DEFAULT_ARTIFACT_ROOT: Final = "/tmp/tracecat-scatter-load"
DEFAULT_BASE_URL: Final = "http://localhost:80/api"
# Defaults match the synthetic users seeded by `scripts/cluster up -d`.
DEFAULT_EMAIL: Final = "dev@tracecat.com"
DEFAULT_PASSWORD: Final = "password1234"
DEFAULT_WORKSPACE_NAME: Final = "scatter-load"
WARMUP_BRANCH_COUNT: Final = 2


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def summarize_latency(values: list[float]) -> LatencySummary:
    ordered = sorted(values)
    return LatencySummary(
        count=len(ordered),
        p50=_percentile(ordered, 0.50),
        p95=_percentile(ordered, 0.95),
        p99=_percentile(ordered, 0.99),
        minimum=ordered[0] if ordered else None,
        maximum=ordered[-1] if ordered else None,
    )


class JsonLinesWriter:
    """Append-only JSON Lines sink, flushed per record."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = path.open("a", encoding="utf-8")
        self._lock = asyncio.Lock()

    async def write(self, kind: str, payload: dict[str, object]) -> None:
        line = json.dumps({"kind": kind, **payload}, default=str)
        async with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class ScatterLoadRunner:
    """Drives warm-up, ramp, sustain, and drain for one scenario."""

    def __init__(
        self,
        client: TracecatClient,
        scenario: ScenarioConfig,
        workflow_id: str,
        writer: JsonLinesWriter,
    ) -> None:
        self._client = client
        self._scenario = scenario
        self._workflow_id = workflow_id
        self._writer = writer
        self._abort = asyncio.Event()
        self._records: list[ExecutionRecord] = []
        self._records_lock = asyncio.Lock()
        self._next_seq = 0
        self._seq_lock = asyncio.Lock()
        self._payload = "x" * scenario.payload_bytes
        self._started_monotonic = 0.0
        self._ramp_deadline = 0.0

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()

    def request_abort(self) -> None:
        """Stop submitting new work. Fixtures are never deleted."""
        self._abort.set()

    async def _claim_seq(self) -> int:
        async with self._seq_lock:
            seq = self._next_seq
            self._next_seq += 1
            return seq

    async def _record(self, record: ExecutionRecord) -> None:
        async with self._records_lock:
            self._records.append(record)
        await self._writer.write("execution", dataclasses.asdict(record))

    async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
        seq = await self._claim_seq()
        record = ExecutionRecord(
            workflow_seq=seq, phase=phase, submitted_at=time.monotonic()
        )
        inputs: dict[str, object] = {
            "run_id": self._scenario.run_id,
            "workflow_seq": seq,
            "branch_count": branch_count,
            "payload": self._payload,
        }

        try:
            result = await self._client.submit_execution(
                self._scenario.workspace_id, self._workflow_id, inputs
            )
        except httpx.HTTPError:
            record.failure_mode = FailureMode.SUBMIT_TRANSPORT_ERROR
            record.terminal_at = time.monotonic()
            await self._record(record)
            return record

        record.submit_status_code = result["status_code"]
        if result["wf_exec_id"] is None:
            record.failure_mode = FailureMode.ADMISSION_REJECTED
            record.detail = result["detail"]
            record.terminal_at = time.monotonic()
            await self._record(record)
            return record

        record.accepted_at = time.monotonic()
        record.wf_exec_id = result["wf_exec_id"]
        await self._poll_to_terminal(record)
        await self._record(record)
        return record

    async def _poll_to_terminal(self, record: ExecutionRecord) -> None:
        """Poll at the fixed scenario interval until terminal, timeout, or abort."""
        wf_exec_id = record.wf_exec_id
        if wf_exec_id is None:
            return
        deadline = record.submitted_at + self._scenario.run_timeout_seconds
        transport_errors = 0

        while True:
            await asyncio.sleep(self._scenario.poll_interval_seconds)
            record.poll_count += 1

            try:
                status = await self._client.get_execution_status(
                    self._scenario.workspace_id, wf_exec_id
                )
            except httpx.HTTPError:
                transport_errors += 1
                status = None

            if status is not None and status in TERMINAL_STATUSES:
                record.terminal_at = time.monotonic()
                record.terminal_status = status
                if status != "COMPLETED":
                    record.failure_mode = FailureMode.WORKFLOW_FAILED
                return

            now = time.monotonic()
            if now >= deadline:
                record.terminal_at = now
                record.terminal_status = status
                record.failure_mode = (
                    FailureMode.POLL_TRANSPORT_ERROR
                    if transport_errors and status is None
                    else FailureMode.RUN_TIMEOUT
                )
                return

            if self._abort.is_set() and self._scenario.abort_stops_polling:
                record.terminal_at = now
                record.terminal_status = status
                record.failure_mode = FailureMode.ABORTED
                return

    async def _worker(self, stagger: float, sustain_deadline: float) -> None:
        """One concurrency slot: ramp in, then keep the slot busy until the deadline."""
        await asyncio.sleep(stagger)
        while not self._abort.is_set():
            phase = (
                Phase.RAMP if time.monotonic() < self._ramp_deadline else Phase.SUSTAIN
            )
            await self._run_one(phase, self._scenario.branch_count)
            if time.monotonic() >= sustain_deadline or self._abort.is_set():
                return

    async def run(self) -> RunSummary:
        self._started_monotonic = time.monotonic()

        if self._scenario.warmup and not self._abort.is_set():
            await self._run_one(Phase.WARMUP, WARMUP_BRANCH_COUNT)

        ramp_start = time.monotonic()
        self._ramp_deadline = ramp_start + self._scenario.ramp_seconds
        sustain_deadline = self._ramp_deadline + self._scenario.steady_state_seconds

        slots = self._scenario.workflow_count
        stagger_step = (
            self._scenario.ramp_seconds / slots
            if slots > 0 and self._scenario.ramp_seconds > 0
            else 0.0
        )
        await asyncio.gather(
            *(
                self._worker(slot * stagger_step, sustain_deadline)
                for slot in range(slots)
            )
        )

        return self.summarize()

    def summarize(self) -> RunSummary:
        wall_clock = time.monotonic() - self._started_monotonic
        load_records = [r for r in self._records if r.phase is not Phase.WARMUP]

        failure_modes = Counter(r.failure_mode.value for r in load_records)
        status_codes = Counter(
            str(r.submit_status_code) if r.submit_status_code is not None else "none"
            for r in load_records
        )
        completed = sum(1 for r in load_records if r.terminal_status == "COMPLETED")
        latencies = [
            r.latency_seconds
            for r in load_records
            if r.terminal_status == "COMPLETED" and r.latency_seconds is not None
        ]
        first_failure = min(
            (
                r.terminal_at
                for r in load_records
                if r.failure_mode is not FailureMode.NONE and r.terminal_at is not None
            ),
            default=None,
        )

        return RunSummary(
            run_id=self._scenario.run_id,
            submitted=len(load_records),
            accepted=sum(1 for r in load_records if r.accepted),
            completed=completed,
            failed=failure_modes[FailureMode.WORKFLOW_FAILED.value],
            timed_out=failure_modes[FailureMode.RUN_TIMEOUT.value],
            aborted=failure_modes[FailureMode.ABORTED.value],
            failure_modes=dict(failure_modes),
            submit_status_codes=dict(status_codes),
            wall_clock_seconds=wall_clock,
            throughput_workflows_per_second=(
                completed / wall_clock if wall_clock > 0 else 0.0
            ),
            latency=summarize_latency(latencies),
            expected_rows=completed * self._scenario.branch_count,
            first_failure_at=(
                first_failure - self._started_monotonic
                if first_failure is not None
                else None
            ),
            aborted_by_signal=self._abort.is_set(),
        )


def render_summary(scenario: ScenarioConfig, summary: RunSummary) -> str:
    latency = summary.latency

    def fmt(value: float | None) -> str:
        return f"{value:.3f}s" if value is not None else "n/a"

    lines = [
        "=== Tracecat PostgreSQL scatter load test ===",
        f"run id                 {summary.run_id}",
        f"write path             {scenario.write_path.value}",
        f"workflows x branches   {scenario.workflow_count} x {scenario.branch_count}",
        f"ramp / sustain         {scenario.ramp_seconds:.0f}s / "
        f"{scenario.steady_state_seconds:.0f}s",
        f"payload bytes          {scenario.payload_bytes}",
        f"poll interval (fixed)  {scenario.poll_interval_seconds:.2f}s",
        f"per-run timeout        {scenario.run_timeout_seconds:.0f}s",
        "",
        f"submitted              {summary.submitted}",
        f"accepted               {summary.accepted}",
        f"completed              {summary.completed}",
        f"failed                 {summary.failed}",
        f"timed out              {summary.timed_out}",
        f"aborted                {summary.aborted}",
        f"wall clock             {summary.wall_clock_seconds:.1f}s",
        f"throughput             {summary.throughput_workflows_per_second:.3f} wf/s",
        "",
        f"latency p50/p95/p99    {fmt(latency.p50)} / {fmt(latency.p95)} / "
        f"{fmt(latency.p99)}",
        f"latency min/max        {fmt(latency.minimum)} / {fmt(latency.maximum)}",
        "",
        f"expected unique rows   {summary.expected_rows} "
        f"(target {scenario.expected_rows} at full success)",
        "  actual row counts are recorded by the metric collector, which holds the",
        "  direct PostgreSQL connection.",
        "",
        f"first failure at       {fmt(summary.first_failure_at)}",
        f"aborted by signal      {summary.aborted_by_signal}",
        "",
        "failure modes:",
    ]
    for mode, count in sorted(summary.failure_modes.items()):
        lines.append(f"  {mode:<28} {count}")
    lines.append("submit status codes:")
    for code, count in sorted(summary.submit_status_codes.items()):
        lines.append(f"  {code:<28} {count}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scatter_load.runner",
        description="Asynchronous API load runner for the PostgreSQL scatter load test.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TRACECAT_SCATTER_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("TRACECAT_SCATTER_EMAIL", DEFAULT_EMAIL),
    )
    parser.add_argument(
        "--password-env",
        default="TRACECAT_SCATTER_PASSWORD",
        help="Env var holding the synthetic user's password.",
    )
    parser.add_argument(
        "--api-key-env",
        default="TRACECAT_SCATTER_API_KEY",
        help=(
            "Env var holding a service-account API key. "
            "Takes precedence over --password-env."
        ),
    )
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--workspace-name", default=DEFAULT_WORKSPACE_NAME)
    parser.add_argument(
        "--write-path",
        choices=[p.value for p in WritePath],
        default=WritePath.SCATTER.value,
    )
    parser.add_argument("--workflow-count", type=int, default=1)
    parser.add_argument("--branch-count", type=int, default=8)
    parser.add_argument("--ramp-seconds", type=float, default=10.0)
    parser.add_argument("--steady-state-seconds", type=float, default=60.0)
    parser.add_argument("--payload-bytes", type=int, default=256)
    parser.add_argument("--run-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=1.0,
        help="Fixed across every phase; recorded in the scenario config.",
    )
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument(
        "--abort-stops-polling",
        action="store_true",
        help="On abort, also stop polling in-flight executions instead of draining them.",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--artifact-root", default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--max-connections",
        type=int,
        default=200,
        help="HTTP connection pool size for the runner itself.",
    )
    return parser


async def amain(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]

    run_id = args.run_id or f"scatter-{uuid.uuid4().hex[:12]}"
    artifact_dir = Path(args.artifact_root) / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get(args.api_key_env) or None
    password = os.environ.get(args.password_env) or DEFAULT_PASSWORD
    auth = AuthConfig(
        email=args.email,
        password=None if api_key else password,
        api_key=api_key,
    )

    writer = JsonLinesWriter(artifact_dir / "runner_results.jsonl")
    try:
        async with TracecatClient(
            args.base_url, api_key=auth.api_key, max_connections=args.max_connections
        ) as client:
            if auth.api_key is None:
                if auth.password is None:
                    print("No credential available", file=sys.stderr)
                    return 2
                await client.login(auth.email, auth.password)

            workspace_id = await resolve_workspace(
                client, args.workspace_id, args.workspace_name
            )
            write_path = WritePath(args.write_path)
            handles = await ensure_fixtures(client, workspace_id, (write_path,))

            scenario = ScenarioConfig(
                run_id=run_id,
                base_url=args.base_url,
                workspace_id=workspace_id,
                write_path=write_path,
                workflow_count=args.workflow_count,
                branch_count=args.branch_count,
                ramp_seconds=args.ramp_seconds,
                steady_state_seconds=args.steady_state_seconds,
                payload_bytes=args.payload_bytes,
                run_timeout_seconds=args.run_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                warmup=not args.no_warmup,
                submit_concurrency=args.workflow_count,
                max_connections=args.max_connections,
                artifact_dir=str(artifact_dir),
                tracecat_commit=_git_commit(repo_root),
                started_at=_utc_now_iso(),
                auth_mode="api_key" if auth.api_key else "password",
                auth_email=auth.email,
                abort_stops_polling=args.abort_stops_polling,
            )

            scenario_payload = dataclasses.asdict(scenario)
            scenario_payload["table_name"] = handles.table_name
            scenario_payload["unique_index_column"] = handles.unique_index_column
            scenario_payload["workflow_id"] = handles.workflow_ids[write_path]
            (artifact_dir / "scenario.json").write_text(
                json.dumps(scenario_payload, indent=2, default=str), encoding="utf-8"
            )
            await writer.write("scenario", scenario_payload)

            runner = ScatterLoadRunner(
                client, scenario, handles.workflow_ids[write_path], writer
            )

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, runner.request_abort)

            summary = await runner.run()

            await writer.write("summary", dataclasses.asdict(summary))
            rendered = render_summary(scenario, summary)
            (artifact_dir / "summary.txt").write_text(rendered + "\n", encoding="utf-8")
            print(rendered)
            print(f"\nartifacts: {artifact_dir}")
            return 1 if summary.aborted_by_signal else 0
    except ApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 2
    finally:
        writer.close()


def main() -> None:
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
