"""Asynchronous public-API runner for Tracecat workflow load types.

Starts workflows through the public admission path
(`POST /workspaces/{workspace_id}/workflow-executions`) and polls them to a
terminal state at a fixed interval. It never calls Temporal directly and never
deletes fixtures during a load run. Its explicit reset mode recreates only the
checked-in synthetic table between experiment cells.

Normally invoked by ``just cluster loadtest``. Direct component usage is
available for harness development and diagnosis:

    uv run --all-packages python -m tracecat_benchmark.runner \\
        --base-url http://localhost:80/api \\
        --cluster-num 1 \\
        --workspace-id 00000000-0000-4000-8000-000000000000 \\
        --run-id scatter-abc123 \\
        --load-type scatter \\
        --workflow-count 8 --branch-count 64 \\
        --ramp-seconds 30 --steady-state-seconds 120

Use ``--existing-deployment`` to exercise an already-running deployment such
as a Kubernetes cluster. That mode deliberately emits runner evidence only;
the Compose collector and its PostgreSQL, Temporal, and container metrics are
not available.

The PostgreSQL scatter plan is the first experiment built on this runner.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import math
import os
import re
import signal
import ssl
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

from tracecat.identifiers.workflow import WorkspaceUUID

from .client import (
    ApiError,
    ExecutionFailureDiagnostic,
    ExecutionStatusRefreshError,
    TracecatClient,
)
from .fixtures import (
    FixtureError,
    ensure_fixtures,
    reset_fixture_table,
    resolve_workspace,
)
from .models import (
    COLLECTOR_MEASUREMENT_COMPLETE_FILENAME,
    COLLECTOR_MEASUREMENT_READY_FILENAME,
    MAX_BULK_BRANCH_COUNT,
    RUNNER_COMPLETE_FILENAME,
    RUNNER_MEASUREMENT_COMPLETE_FILENAME,
    RUNNER_MEASUREMENT_READY_FILENAME,
    TERMINAL_STATUSES,
    ActivityMetricsHandoff,
    AuthConfig,
    ExecutionRecord,
    FailureMode,
    LatencySummary,
    LoadType,
    MeasurementBoundary,
    Phase,
    RunnerComplete,
    RunSummary,
    ScenarioConfig,
    run_id_fingerprint,
    shareable_artifact_path,
    workflow_execution_fingerprint,
    workspace_fingerprint,
)
from .repository import resolve_repository_root

DEFAULT_ARTIFACT_ROOT: Final = "/tmp/tracecat-load-test"
# Defaults match the synthetic users seeded by `scripts/cluster up -d`.
DEFAULT_EMAIL: Final = "dev@tracecat.com"
DEFAULT_PASSWORD: Final = "password1234"
DEFAULT_WORKSPACE_NAME: Final = "load-test"
WARMUP_BRANCH_COUNT: Final = 2
FAILURE_DIAGNOSTICS_CONCURRENCY: Final = 8
COLLECTOR_READY_FILENAME: Final = "collector_ready.json"
RUNNER_ARTIFACT_FILENAMES: Final = (
    "runner_results.jsonl",
    "scenario.json",
    "summary.txt",
    RUNNER_COMPLETE_FILENAME,
)
CASE_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_id_for_phase(run_id: str, phase: Phase) -> str:
    """Keep warm-up writes outside the measured run's correctness totals."""
    return f"{run_id}-warmup" if phase is Phase.WARMUP else run_id


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


def _tracecat_commit_for_target(
    repo_root: Path,
    *,
    existing_deployment: bool,
    deployed_commit: str | None,
) -> str:
    """Record target provenance without mistaking the runner checkout for it."""
    if existing_deployment:
        return (
            deployed_commit.strip()
            if deployed_commit and deployed_commit.strip()
            else "unknown"
        )
    return _git_commit(repo_root)


def _scenario_artifact_payload(scenario: ScenarioConfig) -> dict[str, object]:
    """Serialize a scenario without retaining raw workspace or run identifiers."""
    payload = dataclasses.asdict(scenario)
    raw_workspace_id = str(payload.pop("workspace_id"))
    raw_run_id = str(payload.pop("run_id"))
    payload["run_id"] = run_id_fingerprint(raw_run_id)
    payload["workspace_fingerprint"] = workspace_fingerprint(raw_workspace_id)
    raw_artifact_dir = str(payload["artifact_dir"])
    payload["artifact_dir"] = shareable_artifact_path(
        raw_artifact_dir,
        raw_artifact_dir,
        raw_run_id,
    )
    return payload


def _write_runner_completion(
    artifact_dir: Path,
    run_id: str,
    *,
    completed: bool,
) -> None:
    """Publish runner shutdown only after all preceding artifacts are closed."""
    marker = RunnerComplete(
        run_id=run_id_fingerprint(run_id),
        status="completed" if completed else "aborted",
        completed_at=_utc_now_iso(),
    )
    marker_path = artifact_dir / RUNNER_COMPLETE_FILENAME
    temporary_path = artifact_dir / f".{RUNNER_COMPLETE_FILENAME}.tmp"
    with temporary_path.open("x", encoding="utf-8") as handle:
        json.dump(marker, handle, indent=2)
        handle.write("\n")
    temporary_path.replace(marker_path)


def _write_measurement_boundary(path: Path, run_id: str) -> None:
    marker = MeasurementBoundary(
        run_id=run_id_fingerprint(run_id),
        status="ready",
        recorded_at=_utc_now_iso(),
    )
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("x", encoding="utf-8") as handle:
        json.dump(marker, handle, indent=2)
        handle.write("\n")
    temporary_path.replace(path)


def _write_activity_metrics_handoff(
    path: Path,
    run_id: str,
    summary: RunSummary,
    workflow_execution_ids: list[str],
    workflow_execution_ids_complete: bool,
    *,
    measurement_started_at: str,
    measurement_finished_at: str,
) -> None:
    """Write raw execution IDs only to the matrix's private temporary directory."""
    handoff = ActivityMetricsHandoff(
        run_id=run_id_fingerprint(run_id),
        measurement_window_seconds=summary.wall_clock_seconds,
        measurement_started_at=measurement_started_at,
        measurement_finished_at=measurement_finished_at,
        workflow_execution_ids=workflow_execution_ids,
        workflow_execution_ids_complete=workflow_execution_ids_complete,
    )
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(handoff, handle)
            handle.write("\n")
        path.chmod(0o600)
    except OSError as exc:
        raise RunnerArtifactReuseError(
            f"could not publish private activity metrics handoff: {path}"
        ) from exc


class CollectorReadinessError(RuntimeError):
    """The required metric collector did not become ready for this run."""


class RunnerArtifactReuseError(RuntimeError):
    """Runner-owned artifacts already exist for the requested run ID."""


class WarmupIsolationError(RuntimeError):
    """The warm-up may still be running, so measured load cannot start safely."""


class FailureDiagnosticsCaptureError(RuntimeError):
    """Required per-execution failure diagnostics could not be captured."""


class MissingFailureDiagnosticsError(RuntimeError):
    """A failed execution had no machine-readable failure diagnostic."""


def _reject_existing_runner_artifacts(artifact_dir: Path) -> None:
    existing = tuple(
        name for name in RUNNER_ARTIFACT_FILENAMES if (artifact_dir / name).exists()
    )
    if existing:
        raise RunnerArtifactReuseError(
            "refusing to overwrite or append existing runner artifacts: "
            + ", ".join(existing)
        )


async def _wait_for_collector_ready(
    artifact_dir: Path,
    run_id: str,
    timeout_seconds: float,
    *,
    cluster_num: int,
    public_api_url: str,
    workspace_id: str,
) -> None:
    """Wait until the collector has persisted target-matched initial samples."""
    ready_path = artifact_dir / COLLECTOR_READY_FILENAME
    manifest_path = artifact_dir / "manifest.json"
    expected_run_id = run_id_fingerprint(run_id)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_path.is_file():
            try:
                payload = json.loads(ready_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("run_id") == expected_run_id:
                if payload.get("status") == "ready":
                    mismatched_fields: list[str] = []
                    if payload.get("cluster_num") != cluster_num:
                        mismatched_fields.append("cluster_num")
                    if payload.get("public_api_url") != public_api_url:
                        mismatched_fields.append("public_api_url")
                    if payload.get("workspace_fingerprint") != workspace_fingerprint(
                        workspace_id
                    ):
                        mismatched_fields.append("workspace_fingerprint")
                    if mismatched_fields:
                        raise CollectorReadinessError(
                            "metric collector target does not match runner for: "
                            + ", ".join(mismatched_fields)
                        )
                    if (
                        isinstance(payload.get("sample_count"), int)
                        and payload["sample_count"] >= 1
                        and isinstance(payload.get("temporal_sample_count"), int)
                        and payload["temporal_sample_count"] >= 1
                        and isinstance(payload.get("resource_sample_count"), int)
                        and payload["resource_sample_count"] >= 1
                    ):
                        return

        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = None
            if (
                isinstance(manifest, dict)
                and manifest.get("run_id") == expected_run_id
                and manifest.get("status") == "observability_failed"
            ):
                raise CollectorReadinessError(
                    "metric collector failed before becoming ready"
                )
        await asyncio.sleep(0.1)

    raise CollectorReadinessError(
        f"metric collector was not ready within {timeout_seconds:g} seconds"
    )


async def _synchronize_measurement_baseline(
    artifact_dir: Path,
    run_id: str,
    timeout_seconds: float,
) -> None:
    """Pause after warm-up until the collector snapshots SDK metric baselines."""
    await _synchronize_measurement_boundary(
        artifact_dir,
        run_id,
        timeout_seconds,
        request_filename=RUNNER_MEASUREMENT_READY_FILENAME,
        acknowledgement_filename=COLLECTOR_MEASUREMENT_READY_FILENAME,
        boundary_label="baseline",
    )


async def _synchronize_measurement_complete(
    artifact_dir: Path,
    run_id: str,
    timeout_seconds: float,
) -> None:
    """Pause after measured load until the collector snapshots final SDK metrics."""
    await _synchronize_measurement_boundary(
        artifact_dir,
        run_id,
        timeout_seconds,
        request_filename=RUNNER_MEASUREMENT_COMPLETE_FILENAME,
        acknowledgement_filename=COLLECTOR_MEASUREMENT_COMPLETE_FILENAME,
        boundary_label="final snapshot",
    )


async def _synchronize_measurement_boundary(
    artifact_dir: Path,
    run_id: str,
    timeout_seconds: float,
    *,
    request_filename: str,
    acknowledgement_filename: str,
    boundary_label: str,
) -> None:
    request_path = artifact_dir / request_filename
    acknowledgement_path = artifact_dir / acknowledgement_filename
    manifest_path = artifact_dir / "manifest.json"
    expected_run_id = run_id_fingerprint(run_id)
    _write_measurement_boundary(request_path, run_id)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if acknowledgement_path.is_file():
            try:
                payload: object = json.loads(
                    acknowledgement_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                payload = None
            if (
                isinstance(payload, dict)
                and payload.get("run_id") == expected_run_id
                and payload.get("status") == "ready"
                and isinstance(payload.get("recorded_at"), str)
            ):
                return

        if manifest_path.is_file():
            try:
                manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = None
            if (
                isinstance(manifest, dict)
                and manifest.get("run_id") == expected_run_id
                and manifest.get("status") == "observability_failed"
            ):
                raise CollectorReadinessError(
                    f"metric collector failed before the {boundary_label}"
                )
        await asyncio.sleep(0.05)

    raise CollectorReadinessError(
        f"metric collector did not capture its {boundary_label} within "
        f"{timeout_seconds:g} seconds"
    )


def _execution_record_artifact_payload(
    record: ExecutionRecord,
) -> dict[str, object]:
    """Serialize an execution without retaining its Temporal execution ID."""
    payload: dict[str, object] = dataclasses.asdict(record)
    raw_execution_id = payload.pop("wf_exec_id")
    payload["workflow_execution_fingerprint"] = (
        workflow_execution_fingerprint(raw_execution_id)
        if isinstance(raw_execution_id, str)
        else None
    )
    return payload


def _failure_diagnostic_artifact_payload(
    diagnostic: ExecutionFailureDiagnostic,
) -> ExecutionFailureDiagnostic:
    child_execution_id = diagnostic["child_wf_exec_id"]
    return ExecutionFailureDiagnostic(
        action_ref=diagnostic["action_ref"],
        action_name=diagnostic["action_name"],
        event_type=diagnostic["event_type"],
        status=diagnostic["status"],
        child_wf_exec_id=(
            workflow_execution_fingerprint(child_execution_id)
            if child_execution_id is not None
            else None
        ),
        loop_index=diagnostic["loop_index"],
    )


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
        try:
            self._handle = path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise RunnerArtifactReuseError(
                f"refusing to append existing runner artifact: {path}"
            ) from exc
        self._lock = asyncio.Lock()

    async def write(self, kind: str, payload: dict[str, object]) -> None:
        line = json.dumps({"kind": kind, **payload}, default=str)
        async with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class LoadRunner:
    """Drives warm-up, ramp, sustain, and drain for one scenario."""

    def __init__(
        self,
        client: TracecatClient,
        scenario: ScenarioConfig,
        workflow_id: str,
        writer: JsonLinesWriter,
        *,
        warmup_workflow_id: str | None = None,
    ) -> None:
        self._client = client
        self._scenario = scenario
        self._workflow_id = workflow_id
        self._warmup_workflow_id = warmup_workflow_id or workflow_id
        self._writer = writer
        self._abort = asyncio.Event()
        self._records: list[ExecutionRecord] = []
        self._records_lock = asyncio.Lock()
        self._next_seq = 0
        self._seq_lock = asyncio.Lock()
        self._payload = "x" * scenario.payload_bytes
        self._started_monotonic = 0.0
        self._ramp_deadline = 0.0
        self._measurement_started_at: str | None = None
        self._measurement_finished_at: str | None = None

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
        await self._writer.write(
            "execution",
            _execution_record_artifact_payload(record),
        )

    def measured_workflow_execution_ids(self) -> list[str]:
        """Return accepted non-warm-up executions for the private handoff."""
        return [
            record.wf_exec_id
            for record in self._records
            if record.phase is not Phase.WARMUP and record.wf_exec_id is not None
        ]

    def measured_workflow_execution_ids_complete(self) -> bool:
        """Whether every possibly accepted measured admission has an ID."""
        return all(
            record.wf_exec_id is not None
            or record.failure_mode is FailureMode.ADMISSION_REJECTED
            for record in self._records
            if record.phase is not Phase.WARMUP
        )

    def measurement_timestamps(self) -> tuple[str, str]:
        """Return the exact UTC boundaries corresponding to the run summary."""
        if (
            self._measurement_started_at is None
            or self._measurement_finished_at is None
        ):
            raise RuntimeError("measured load has not completed")
        return self._measurement_started_at, self._measurement_finished_at

    async def _capture_failure_diagnostics(self) -> None:
        """Fetch per-execution failure evidence after measured load has stopped."""
        failed_records = [
            record
            for record in self._records
            if record.failure_mode is FailureMode.WORKFLOW_FAILED
            and record.wf_exec_id is not None
        ]
        semaphore = asyncio.Semaphore(FAILURE_DIAGNOSTICS_CONCURRENCY)

        async def capture(record: ExecutionRecord) -> str | None:
            wf_exec_id = record.wf_exec_id
            if wf_exec_id is None:
                return None
            diagnostics: list[ExecutionFailureDiagnostic] = []
            capture_error: str | None = None
            try:
                async with semaphore:
                    diagnostics = await self._client.get_execution_failure_diagnostics(
                        self._scenario.workspace_id,
                        wf_exec_id,
                    )
            except (httpx.HTTPError, ExecutionStatusRefreshError) as exc:
                capture_error = type(exc).__name__
            else:
                if not diagnostics:
                    capture_error = MissingFailureDiagnosticsError.__name__
            await self._writer.write(
                "execution_failure_diagnostics",
                {
                    "workflow_seq": record.workflow_seq,
                    "workflow_execution_fingerprint": (
                        workflow_execution_fingerprint(wf_exec_id)
                    ),
                    "terminal_status": record.terminal_status,
                    "diagnostics": [
                        _failure_diagnostic_artifact_payload(diagnostic)
                        for diagnostic in diagnostics
                    ],
                    "capture_error": capture_error,
                },
            )
            return capture_error

        capture_errors = [
            error
            for error in await asyncio.gather(
                *(capture(record) for record in failed_records)
            )
            if error is not None
        ]
        if capture_errors:
            error_counts = Counter(capture_errors)
            details = ", ".join(
                f"{name}={count}" for name, count in sorted(error_counts.items())
            )
            raise FailureDiagnosticsCaptureError(
                "required failure diagnostics were unavailable for "
                f"{len(capture_errors)} execution(s): {details}"
            )

    async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
        seq = await self._claim_seq()
        record = ExecutionRecord(
            workflow_seq=seq, phase=phase, submitted_at=time.monotonic()
        )
        inputs: dict[str, object] = {
            "run_id": _run_id_for_phase(self._scenario.run_id, phase),
            "workflow_seq": seq,
            "payload": self._payload,
        }
        if not self._scenario.load_type.materializes_static_actions:
            inputs["branch_count"] = branch_count
        deadline = record.submitted_at + self._scenario.run_timeout_seconds

        try:
            async with asyncio.timeout(deadline - time.monotonic()):
                workflow_id = (
                    self._warmup_workflow_id
                    if phase is Phase.WARMUP
                    else self._workflow_id
                )
                result = await self._client.submit_execution(
                    self._scenario.workspace_id, workflow_id, inputs
                )
        except TimeoutError:
            record.failure_mode = FailureMode.SUBMIT_TIMEOUT
            record.terminal_at = time.monotonic()
            await self._record(record)
            return record
        except httpx.HTTPError:
            record.failure_mode = FailureMode.SUBMIT_TRANSPORT_ERROR
            record.terminal_at = time.monotonic()
            await self._record(record)
            return record

        record.submit_status_code = result["status_code"]
        now = time.monotonic()
        if now >= deadline:
            record.accepted_at = now if result["wf_exec_id"] is not None else None
            record.wf_exec_id = result["wf_exec_id"]
            record.failure_mode = FailureMode.SUBMIT_TIMEOUT
            record.terminal_at = now
            await self._record(record)
            return record
        if result["wf_exec_id"] is None:
            record.failure_mode = (
                FailureMode.ADMISSION_OUTCOME_UNKNOWN
                if 200 <= result["status_code"] < 300
                else FailureMode.ADMISSION_REJECTED
            )
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
        last_status: str | None = None
        poll_interval = self._scenario.poll_interval_seconds
        next_poll_at = time.monotonic() + poll_interval

        def record_timeout(now: float) -> None:
            record.terminal_at = now
            record.terminal_status = last_status
            record.failure_mode = (
                FailureMode.POLL_TRANSPORT_ERROR
                if transport_errors and last_status is None
                else FailureMode.RUN_TIMEOUT
            )

        while True:
            now = time.monotonic()
            if now >= deadline:
                record_timeout(now)
                return
            await asyncio.sleep(max(0.0, min(next_poll_at, deadline) - now))
            now = time.monotonic()
            if now >= deadline:
                record_timeout(now)
                return
            record.poll_count += 1

            try:
                async with asyncio.timeout(deadline - now):
                    snapshot = await self._client.get_execution_status(
                        self._scenario.workspace_id, self._workflow_id, wf_exec_id
                    )
            except TimeoutError:
                record_timeout(time.monotonic())
                return
            except (httpx.HTTPError, ExecutionStatusRefreshError):
                transport_errors += 1
                snapshot = None

            now = time.monotonic()
            if now >= deadline:
                record_timeout(now)
                return
            status = snapshot["status"] if snapshot is not None else None
            if snapshot is not None:
                last_status = status
                record.history_length = snapshot["history_length"]

            if status is not None and status in TERMINAL_STATUSES:
                record.terminal_at = time.monotonic()
                record.terminal_status = status
                if status != "COMPLETED":
                    record.failure_mode = FailureMode.WORKFLOW_FAILED
                return

            if self._abort.is_set() and self._scenario.abort_stops_polling:
                record.terminal_at = now
                record.terminal_status = last_status
                record.failure_mode = FailureMode.ABORTED
                return

            # Keep the configured start-to-start cadence. A slow API request
            # consumes the interval instead of adding another full sleep.
            next_poll_at = max(next_poll_at + poll_interval, now)

    async def _worker(self, stagger: float, sustain_deadline: float) -> None:
        """One concurrency slot: ramp in, then keep the slot busy until the deadline."""
        if stagger > 0:
            try:
                await asyncio.wait_for(self._abort.wait(), timeout=stagger)
            except TimeoutError:
                pass
            else:
                return
        while not self._abort.is_set():
            phase = (
                Phase.RAMP if time.monotonic() < self._ramp_deadline else Phase.SUSTAIN
            )
            record = await self._run_one(phase, self._scenario.branch_count)
            if self._scenario.one_shot:
                return
            if self._may_still_be_running(record):
                # The server may still be running this execution. Retire the
                # slot instead of exceeding the requested concurrency with a
                # replacement that cannot be proven safe.
                return
            if time.monotonic() >= sustain_deadline or self._abort.is_set():
                return

    @staticmethod
    def _may_still_be_running(record: ExecutionRecord) -> bool:
        """Return whether replacing this record could exceed the load bound."""
        return record.failure_mode in {
            FailureMode.ADMISSION_OUTCOME_UNKNOWN,
            FailureMode.SUBMIT_TIMEOUT,
            FailureMode.SUBMIT_TRANSPORT_ERROR,
        } or (
            record.wf_exec_id is not None
            and record.terminal_status not in TERMINAL_STATUSES
        )

    async def run(
        self,
        measurement_ready: Callable[[], Awaitable[None]] | None = None,
        measurement_complete: Callable[[], Awaitable[None]] | None = None,
    ) -> RunSummary:
        if self._scenario.warmup and not self._abort.is_set():
            warmup_record = await self._run_one(Phase.WARMUP, WARMUP_BRANCH_COUNT)
            if self._may_still_be_running(warmup_record):
                raise WarmupIsolationError(
                    "warm-up did not reach a terminal state; refusing to overlap "
                    "it with measured load"
                )

        if measurement_ready is not None:
            await measurement_ready()

        self._measurement_started_at = _utc_now_iso()
        ramp_start = time.monotonic()
        self._started_monotonic = ramp_start
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

        summary = self.summarize()
        self._measurement_finished_at = _utc_now_iso()
        if measurement_complete is not None:
            await measurement_complete()
        await self._capture_failure_diagnostics()
        return summary

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

        expected_rows_per_workflow = (
            self._scenario.branch_count
            if self._scenario.load_type.writes_fixture_rows
            else 0
        )
        return RunSummary(
            run_id=run_id_fingerprint(self._scenario.run_id),
            submitted=len(load_records),
            accepted=sum(1 for r in load_records if r.accepted),
            completed=completed,
            failed=failure_modes[FailureMode.WORKFLOW_FAILED.value],
            timed_out=sum(
                failure_modes[mode.value]
                for mode in (
                    FailureMode.SUBMIT_TIMEOUT,
                    FailureMode.RUN_TIMEOUT,
                    FailureMode.POLL_TRANSPORT_ERROR,
                )
            ),
            aborted=failure_modes[FailureMode.ABORTED.value],
            failure_modes=dict(failure_modes),
            submit_status_codes=dict(status_codes),
            wall_clock_seconds=wall_clock,
            throughput_workflows_per_second=(
                completed / wall_clock if wall_clock > 0 else 0.0
            ),
            latency=summarize_latency(latencies),
            expected_rows=completed * expected_rows_per_workflow,
            submitted_row_target=len(load_records) * expected_rows_per_workflow,
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

    row_evidence_lines = (
        [
            "  actual row counts are recorded by the metric collector, which holds the",
            "  direct PostgreSQL connection.",
        ]
        if scenario.evidence_mode == "compose_collector"
        else [
            "  actual row correctness is unavailable in runner-only evidence;",
            "  completed workflow status is not a direct PostgreSQL row count.",
        ]
    )
    lines = [
        "=== Tracecat workflow load test ===",
        f"run id                 {summary.run_id}",
        f"load type             {scenario.load_type.value}",
        f"workflows x branches   {scenario.workflow_count} x {scenario.branch_count}",
        f"ramp / sustain         {scenario.ramp_seconds:.0f}s / "
        f"{scenario.steady_state_seconds:.0f}s",
        f"payload bytes          {scenario.payload_bytes}",
        f"poll interval (fixed)  {scenario.poll_interval_seconds:.2f}s",
        f"per-run timeout        {scenario.run_timeout_seconds:.0f}s",
        f"submission mode        {'one-shot' if scenario.one_shot else 'sustained'}",
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
        f"(from {summary.completed} completed workflows)",
        f"submitted-row target   {summary.submitted_row_target} "
        f"(if all {summary.submitted} submitted workflows succeed)",
        *row_evidence_lines,
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
        prog="tracecat_benchmark.runner",
        description="Asynchronous public-API runner for Tracecat workflow load types.",
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Exact API URL for the numbered load-test cluster selected for this run.",
    )
    parser.add_argument(
        "--cluster-num",
        type=int,
        default=None,
        help=(
            "Number of the cluster selected for this run. Required outside "
            "--bootstrap-workspace and matched against collector readiness."
        ),
    )
    parser.add_argument(
        "--existing-deployment",
        action="store_true",
        help=(
            "Target an already-running deployment through its public API. "
            "Skips Compose collector readiness and emits runner-only evidence."
        ),
    )
    parser.add_argument(
        "--deployed-commit",
        default=None,
        help=(
            "Revision actually running in an existing deployment. Omit it to "
            "record unknown rather than the runner checkout's revision."
        ),
    )
    parser.add_argument(
        "--tls-ca-file",
        default=None,
        help="PEM CA bundle used to verify the selected deployment's HTTPS API.",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("TRACECAT_LOADTEST_EMAIL", DEFAULT_EMAIL),
    )
    parser.add_argument(
        "--password-env",
        default="TRACECAT_LOADTEST_PASSWORD",
        help="Env var holding the synthetic user's password.",
    )
    parser.add_argument(
        "--api-key-env",
        default="TRACECAT_LOADTEST_API_KEY",
        help=(
            "Env var holding a service-account API key. "
            "Takes precedence over --password-env."
        ),
    )
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--workspace-name", default=DEFAULT_WORKSPACE_NAME)
    parser.add_argument(
        "--load-type",
        choices=[p.value for p in LoadType],
        default=LoadType.SCATTER.value,
        help=(
            "Runnable workload family. 'scatter' statically materializes "
            "branch-count independent actions per workflow, 'bulk' is the "
            "batching control, and 'subflow' fans out over child workflows. "
            "Each type uses the same runner lifecycle."
        ),
    )
    parser.add_argument("--workflow-count", type=int, default=1)
    parser.add_argument("--branch-count", type=int, default=1)
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
        "--one-shot",
        action="store_true",
        help=(
            "Submit at most one workflow per slot; required for burst/drain "
            "cells so completed work is not replenished."
        ),
    )
    parser.add_argument(
        "--abort-stops-polling",
        action="store_true",
        help="On abort, also stop polling in-flight executions instead of draining them.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Shared run ID used by the collector and runner artifact directory.",
    )
    parser.add_argument(
        "--case-id",
        default=None,
        help="Human-readable matrix case slug retained in run artifacts.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--bootstrap-workspace",
        action="store_true",
        help=(
            "Create or reuse --workspace-name through the public API, print its "
            "ID, and exit. Run this before starting the collector on a fresh cluster."
        ),
    )
    mode.add_argument(
        "--reset-fixture-table",
        action="store_true",
        help=(
            "Verify, delete, and recreate only the checked-in synthetic table "
            "through the public API, then exit. Requires --workspace-id."
        ),
    )
    parser.add_argument("--artifact-root", default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--activity-metrics-handoff",
        default=None,
        help=(
            "Private temporary JSON path used to hand measured workflow execution "
            "IDs to the post-recovery collector. The matrix supplies this path "
            "and deletes its temporary directory after the run."
        ),
    )
    parser.add_argument(
        "--collector-ready-timeout-seconds",
        type=float,
        default=120.0,
        help=(
            "Wait for collector_ready.json before authentication or fixture "
            "setup, ensuring PostgreSQL and Temporal sampling precede load."
        ),
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=200,
        help="HTTP connection pool size for the runner itself.",
    )
    return parser


async def amain(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    repo_root = resolve_repository_root()

    tls_verify: ssl.SSLContext | bool = True
    if args.tls_ca_file is not None:
        tls_ca_file = Path(args.tls_ca_file)
        if not tls_ca_file.is_file():
            print("--tls-ca-file must name a readable file", file=sys.stderr)
            return 2
        try:
            tls_verify = ssl.create_default_context(cafile=tls_ca_file)
        except (OSError, ssl.SSLError) as exc:
            print(f"--tls-ca-file could not be loaded: {exc}", file=sys.stderr)
            return 2
    if args.max_connections <= 0:
        print("--max-connections must be positive", file=sys.stderr)
        return 2
    if args.bootstrap_workspace:
        api_key = os.environ.get(args.api_key_env) or None
        password = os.environ.get(args.password_env) or DEFAULT_PASSWORD
        try:
            async with TracecatClient(
                args.base_url,
                api_key=api_key,
                max_connections=args.max_connections,
                verify=tls_verify,
            ) as client:
                if api_key is None:
                    await client.login(args.email, password)
                workspace_id = await resolve_workspace(
                    client,
                    args.workspace_id,
                    args.workspace_name,
                )
        except (ApiError, httpx.HTTPError) as exc:
            print(f"Workspace bootstrap API error: {exc}", file=sys.stderr)
            return 2
        print(workspace_id)
        return 0

    if args.reset_fixture_table:
        if args.workspace_id is None:
            print(
                "--workspace-id is required with --reset-fixture-table",
                file=sys.stderr,
            )
            return 2
        try:
            workspace_id = str(WorkspaceUUID.new(args.workspace_id))
        except ValueError:
            print("--workspace-id must be a valid workspace ID", file=sys.stderr)
            return 2
        api_key = os.environ.get(args.api_key_env) or None
        password = os.environ.get(args.password_env) or DEFAULT_PASSWORD
        try:
            async with TracecatClient(
                args.base_url,
                api_key=api_key,
                max_connections=args.max_connections,
                verify=tls_verify,
            ) as client:
                if api_key is None:
                    await client.login(args.email, password)
                table_name = await reset_fixture_table(client, workspace_id)
        except (ApiError, FixtureError, httpx.HTTPError) as exc:
            print(f"Fixture reset error: {exc}", file=sys.stderr)
            return 2
        print(
            f"reset fixture table '{table_name}' in workspace "
            f"{workspace_fingerprint(workspace_id)}"
        )
        return 0

    if args.run_id is None:
        print(
            "--run-id is required unless --bootstrap-workspace is used",
            file=sys.stderr,
        )
        return 2
    run_id = args.run_id
    if args.case_id is not None and CASE_ID_RE.fullmatch(args.case_id) is None:
        print(f"--case-id must match {CASE_ID_RE.pattern!r}", file=sys.stderr)
        return 2
    if args.workflow_count <= 0 or args.branch_count <= 0:
        print(
            "--workflow-count and --branch-count must both be positive",
            file=sys.stderr,
        )
        return 2
    if (
        args.load_type == LoadType.BULK.value
        and args.branch_count > MAX_BULK_BRANCH_COUNT
    ):
        print(
            f"--branch-count must be at most {MAX_BULK_BRANCH_COUNT} for bulk loads",
            file=sys.stderr,
        )
        return 2
    if (
        not math.isfinite(args.ramp_seconds)
        or args.ramp_seconds < 0
        or not math.isfinite(args.steady_state_seconds)
        or args.steady_state_seconds < 0
    ):
        print(
            "--ramp-seconds and --steady-state-seconds must be finite and nonnegative",
            file=sys.stderr,
        )
        return 2
    if not math.isfinite(args.run_timeout_seconds) or args.run_timeout_seconds <= 0:
        print(
            "--run-timeout-seconds must be finite and positive",
            file=sys.stderr,
        )
        return 2
    if args.payload_bytes < 0:
        print("--payload-bytes must be nonnegative", file=sys.stderr)
        return 2
    if not math.isfinite(args.poll_interval_seconds) or args.poll_interval_seconds <= 0:
        print(
            "--poll-interval-seconds must be finite and positive",
            file=sys.stderr,
        )
        return 2
    if not math.isfinite(args.collector_ready_timeout_seconds) or (
        args.collector_ready_timeout_seconds <= 0
    ):
        print(
            "--collector-ready-timeout-seconds must be finite and positive",
            file=sys.stderr,
        )
        return 2
    if args.existing_deployment and args.cluster_num is not None:
        print(
            "--cluster-num cannot be used with --existing-deployment",
            file=sys.stderr,
        )
        return 2
    if args.deployed_commit is not None and not args.existing_deployment:
        print(
            "--deployed-commit can only be used with --existing-deployment",
            file=sys.stderr,
        )
        return 2
    if not args.existing_deployment and (
        args.cluster_num is None or not 1 <= args.cluster_num <= 99
    ):
        print(
            "--cluster-num must be between 1 and 99 unless "
            "--bootstrap-workspace or --existing-deployment is used",
            file=sys.stderr,
        )
        return 2
    if args.workspace_id is None:
        print(
            "--workspace-id is required unless --bootstrap-workspace is used",
            file=sys.stderr,
        )
        return 2
    try:
        expected_workspace_id = str(WorkspaceUUID.new(args.workspace_id))
    except ValueError:
        print("--workspace-id must be a valid workspace ID", file=sys.stderr)
        return 2
    public_api_url = args.base_url.rstrip("/")
    artifact_dir = Path(args.artifact_root) / run_id_fingerprint(run_id)
    activity_metrics_handoff = (
        Path(args.activity_metrics_handoff)
        if args.activity_metrics_handoff is not None
        else None
    )
    if args.existing_deployment and activity_metrics_handoff is not None:
        print(
            "--activity-metrics-handoff cannot be used with "
            "--existing-deployment because no collector is running",
            file=sys.stderr,
        )
        return 2
    if activity_metrics_handoff is not None and activity_metrics_handoff.exists():
        print(
            "Activity metrics handoff already exists; refusing to overwrite it",
            file=sys.stderr,
        )
        return 2
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        _reject_existing_runner_artifacts(artifact_dir)
        writer = JsonLinesWriter(artifact_dir / "runner_results.jsonl")
    except RunnerArtifactReuseError as exc:
        print(f"Runner artifact error: {exc}", file=sys.stderr)
        return 2

    completed = False
    startup_abort_requested = False
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    installed_signals: list[signal.Signals] = []

    def request_startup_abort() -> None:
        nonlocal startup_abort_requested
        startup_abort_requested = True
        if current_task is not None:
            current_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_startup_abort)
        except NotImplementedError:
            continue
        installed_signals.append(sig)

    try:
        if args.existing_deployment:
            print(
                "Existing-deployment mode: Compose collector disabled; "
                "artifacts contain runner evidence only.",
                file=sys.stderr,
            )
        else:
            assert args.cluster_num is not None
            await _wait_for_collector_ready(
                artifact_dir,
                run_id,
                args.collector_ready_timeout_seconds,
                cluster_num=args.cluster_num,
                public_api_url=public_api_url,
                workspace_id=expected_workspace_id,
            )

        api_key = os.environ.get(args.api_key_env) or None
        password = os.environ.get(args.password_env) or DEFAULT_PASSWORD
        auth = AuthConfig(
            email=args.email,
            password=None if api_key else password,
            api_key=api_key,
        )

        async with TracecatClient(
            public_api_url,
            api_key=auth.api_key,
            max_connections=args.max_connections,
            execution_poll_interval_seconds=args.poll_interval_seconds,
            verify=tls_verify,
        ) as client:
            if auth.api_key is None:
                if auth.password is None:
                    print("No credential available", file=sys.stderr)
                    return 2
                await client.login(auth.email, auth.password)

            workspace_id = await resolve_workspace(
                client, expected_workspace_id, args.workspace_name
            )
            load_type = LoadType(args.load_type)
            handles = await ensure_fixtures(
                client,
                workspace_id,
                (load_type,),
                branch_count=args.branch_count,
                warmup_branch_count=(
                    WARMUP_BRANCH_COUNT if not args.no_warmup else None
                ),
            )

            scenario = ScenarioConfig(
                run_id=run_id,
                base_url=public_api_url,
                cluster_num=args.cluster_num,
                workspace_id=workspace_id,
                load_type=load_type,
                workflow_count=args.workflow_count,
                branch_count=args.branch_count,
                ramp_seconds=args.ramp_seconds,
                steady_state_seconds=args.steady_state_seconds,
                payload_bytes=args.payload_bytes,
                run_timeout_seconds=args.run_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                warmup=not args.no_warmup,
                one_shot=args.one_shot,
                collector_ready_timeout_seconds=args.collector_ready_timeout_seconds,
                submit_concurrency=args.workflow_count,
                max_connections=args.max_connections,
                artifact_dir=str(artifact_dir),
                tracecat_commit=_tracecat_commit_for_target(
                    repo_root,
                    existing_deployment=args.existing_deployment,
                    deployed_commit=args.deployed_commit,
                ),
                started_at=_utc_now_iso(),
                auth_mode="api_key" if auth.api_key else "password",
                abort_stops_polling=args.abort_stops_polling,
                evidence_mode=(
                    "runner_only" if args.existing_deployment else "compose_collector"
                ),
                case_id=args.case_id,
            )

            scenario_payload = _scenario_artifact_payload(scenario)
            scenario_payload["table_name"] = handles.table_name
            scenario_payload["unique_index_column"] = handles.unique_index_column
            scenario_payload["workflow_id"] = handles.workflow_ids[load_type]
            scenario_payload["warmup_workflow_id"] = (
                handles.warmup_workflow_ids[load_type] if scenario.warmup else None
            )
            scenario_payload["warmup_run_id"] = (
                run_id_fingerprint(_run_id_for_phase(run_id, Phase.WARMUP))
                if scenario.warmup
                else None
            )
            (artifact_dir / "scenario.json").write_text(
                json.dumps(scenario_payload, indent=2, default=str), encoding="utf-8"
            )
            await writer.write("scenario", scenario_payload)

            runner = LoadRunner(
                client,
                scenario,
                handles.workflow_ids[load_type],
                writer,
                warmup_workflow_id=handles.warmup_workflow_ids[load_type],
            )

            for sig in installed_signals:
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, runner.request_abort)

            measurement_ready: Callable[[], Awaitable[None]] | None = None
            measurement_complete: Callable[[], Awaitable[None]] | None = None
            if activity_metrics_handoff is not None:

                async def synchronize_measurement_start() -> None:
                    await _synchronize_measurement_baseline(
                        artifact_dir,
                        run_id,
                        args.collector_ready_timeout_seconds,
                    )

                async def synchronize_measurement_end() -> None:
                    await _synchronize_measurement_complete(
                        artifact_dir,
                        run_id,
                        args.collector_ready_timeout_seconds,
                    )

                measurement_ready = synchronize_measurement_start
                measurement_complete = synchronize_measurement_end
            summary = await runner.run(measurement_ready, measurement_complete)

            await writer.write("summary", dataclasses.asdict(summary))
            if activity_metrics_handoff is not None:
                measurement_started_at, measurement_finished_at = (
                    runner.measurement_timestamps()
                )
                _write_activity_metrics_handoff(
                    activity_metrics_handoff,
                    run_id,
                    summary,
                    runner.measured_workflow_execution_ids(),
                    runner.measured_workflow_execution_ids_complete(),
                    measurement_started_at=measurement_started_at,
                    measurement_finished_at=measurement_finished_at,
                )
            rendered = render_summary(scenario, summary)
            (artifact_dir / "summary.txt").write_text(rendered + "\n", encoding="utf-8")
            print(rendered)
            print(f"\nartifacts: {artifact_dir}")
            completed = not summary.aborted_by_signal
            return 1 if summary.aborted_by_signal else 0
    except asyncio.CancelledError:
        if not startup_abort_requested:
            raise
        print("Load run aborted by signal during startup", file=sys.stderr)
        return 1
    except CollectorReadinessError as exc:
        print(f"Collector readiness error: {exc}", file=sys.stderr)
        return 2
    except WarmupIsolationError as exc:
        print(f"Load run error: {exc}", file=sys.stderr)
        return 2
    except FailureDiagnosticsCaptureError as exc:
        print(f"Failure diagnostics error: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"API transport error: {exc}", file=sys.stderr)
        return 2
    finally:
        writer.close()
        _write_runner_completion(
            artifact_dir,
            run_id,
            completed=completed,
        )
        for sig in installed_signals:
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)


def main() -> None:
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
