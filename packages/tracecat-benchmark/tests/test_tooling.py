from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import asyncpg
import httpx
import pytest
import yaml
from google.protobuf.duration_pb2 import Duration
from temporalio.api.taskqueue.v1 import TaskQueueStats
from temporalio.api.workflowservice.v1 import (
    DescribeTaskQueueRequest,
    DescribeTaskQueueResponse,
)
from temporalio.client import Client as TemporalClient
from tracecat_benchmark import collector as collector_module
from tracecat_benchmark import fixtures as fixtures_module
from tracecat_benchmark import kubernetes as kubernetes_module
from tracecat_benchmark import matrix as matrix_module
from tracecat_benchmark import provision_monitor as provision_monitor_module
from tracecat_benchmark import runner as runner_module
from tracecat_benchmark.activity_metrics import ActivityMetricsCaptureError
from tracecat_benchmark.client import (
    ExecutionFailureDiagnostic,
    ExecutionStatusRefreshError,
    TracecatClient,
)
from tracecat_benchmark.collector import (
    ACTIVITY_SQL,
    DEFAULT_LOG_SERVICES,
    REDACTED_ENV_VALUE,
    REDACTED_PATH_VALUE,
    WORKSPACE_RLS_CONTEXT_SQL,
    ClusterPorts,
    ComposePublicUrls,
    DockerResourceSampler,
    MetricCollector,
    PgSampler,
    TemporalSampler,
    _application_name_artifact_label,
    _redact_compose_config,
    _resolve_log_services,
    _workspace_schema_name,
    capture_compose_config,
    capture_container_state,
    capture_service_logs,
    resolve_cluster_ports,
    validate_monitor_dsn_target,
    validate_public_api_url,
    validate_running_compose_project,
    validate_temporal_context,
    validate_temporal_sdk_metrics_urls,
)
from tracecat_benchmark.collector import (
    amain as collector_amain,
)
from tracecat_benchmark.models import (
    ARTIFACT_ROOT_PLACEHOLDER,
    RUNNER_COMPLETE_FILENAME,
    CollectorConfig,
    CollectorManifest,
    ContainerResourceUsage,
    ExecutionRecord,
    FailureMode,
    HostResourceUsage,
    LoadType,
    PgActivitySample,
    Phase,
    ResourceUsageSample,
    RowCorrectness,
    RunSummary,
    ScenarioConfig,
    TableDrift,
    TemporalBacklogSample,
    TemporalTaskQueueStats,
    WorkflowFixture,
    compose_project_fingerprint,
    deployment_value_fingerprint,
    run_id_fingerprint,
    shareable_artifact_path,
    workflow_execution_fingerprint,
    workspace_fingerprint,
)
from tracecat_benchmark.provision_monitor import (
    DEFAULT_PROVISION_DSN_ENV,
    provision_monitor_role,
)
from tracecat_benchmark.repository import resolve_repository_root
from tracecat_benchmark.runner import (
    CollectorReadinessError,
    FailureDiagnosticsCaptureError,
    JsonLinesWriter,
    LoadRunner,
    MissingFailureDiagnosticsError,
    WarmupIsolationError,
    _run_id_for_phase,
    _scenario_artifact_payload,
    _wait_for_collector_ready,
    _write_runner_completion,
    render_summary,
)
from tracecat_benchmark.runner import (
    build_parser as build_runner_parser,
)

REPO_ROOT = resolve_repository_root(Path(__file__))


def test_warmup_uses_distinct_run_id() -> None:
    run_id = "scatter-test"

    assert _run_id_for_phase(run_id, Phase.WARMUP) == "scatter-test-warmup"
    assert _run_id_for_phase(run_id, Phase.RAMP) == run_id
    assert _run_id_for_phase(run_id, Phase.SUSTAIN) == run_id


@pytest.mark.parametrize(
    "application_name",
    ["api", "worker-auth", "load-test-collector", "unknown"],
)
def test_fixed_postgres_application_names_remain_attributable(
    application_name: str,
) -> None:
    assert _application_name_artifact_label(application_name) == application_name


def test_unrecognized_postgres_application_names_are_fingerprinted() -> None:
    application_name = "customer-derived-client-label"

    assert _application_name_artifact_label(
        application_name
    ) == deployment_value_fingerprint(application_name)


def test_activity_metrics_require_ids_for_possibly_accepted_admissions(
    tmp_path: Path,
) -> None:
    writer = JsonLinesWriter(tmp_path / "runner.jsonl")
    runner = LoadRunner(
        cast(TracecatClient, object()),
        _scenario_config(
            tmp_path,
            warmup=False,
            workflow_count=2,
            one_shot=True,
        ),
        "workflow-1",
        writer,
    )
    try:
        runner._records = [
            ExecutionRecord(
                workflow_seq=0,
                phase=Phase.RAMP,
                submitted_at=0.0,
                wf_exec_id="execution-1",
            ),
            ExecutionRecord(
                workflow_seq=1,
                phase=Phase.RAMP,
                submitted_at=0.0,
                failure_mode=FailureMode.ADMISSION_OUTCOME_UNKNOWN,
            ),
        ]

        assert not runner.measured_workflow_execution_ids_complete()

        runner._records[1].failure_mode = FailureMode.ADMISSION_REJECTED
        assert runner.measured_workflow_execution_ids_complete()
    finally:
        writer.close()


def test_collector_rejects_incomplete_activity_metrics_handoff(
    tmp_path: Path,
) -> None:
    handoff_path = tmp_path / "activity-metrics-handoff.json"
    handoff_path.write_text(
        json.dumps(
            {
                "run_id": run_id_fingerprint("scatter-test"),
                "measurement_window_seconds": 1.0,
                "measurement_started_at": "2026-01-01T00:00:00+00:00",
                "measurement_finished_at": "2026-01-01T00:00:01+00:00",
                "workflow_execution_ids": ["execution-1"],
                "workflow_execution_ids_complete": False,
            }
        ),
        encoding="utf-8",
    )
    collector = MetricCollector(
        replace(
            _collector_config(tmp_path),
            execution_id_handoff_path=str(handoff_path),
            temporal_executor_task_queue="executor-queue",
            temporal_workflow_task_queues=("workflow-queue",),
        )
    )

    with pytest.raises(
        ActivityMetricsCaptureError,
        match="admission outcome has no workflow execution ID",
    ):
        asyncio.run(collector._capture_activity_metrics())

    assert not handoff_path.exists()


@pytest.mark.parametrize(
    ("completed", "expected_status"),
    [(True, "completed"), (False, "aborted")],
)
def test_runner_completion_marker_is_published_atomically(
    tmp_path: Path,
    completed: bool,
    expected_status: str,
) -> None:
    _write_runner_completion(tmp_path, "scatter-test", completed=completed)

    marker = json.loads((tmp_path / RUNNER_COMPLETE_FILENAME).read_text())
    assert marker["run_id"] == run_id_fingerprint("scatter-test")
    assert marker["status"] == expected_status
    assert marker["completed_at"]
    assert not (tmp_path / f".{RUNNER_COMPLETE_FILENAME}.tmp").exists()


def test_workspace_schema_name_targets_exact_workspace() -> None:
    assert (
        _workspace_schema_name("00000000-0000-4000-8000-000000000000")
        == "tables_ws_000000001vGeH72LxVtxKg"
    )
    assert (
        _workspace_schema_name("ws_0000000000000000000000")
        == "tables_ws_0000000000000000000000"
    )


def test_monitor_provisioning_grants_current_and_future_fixture_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []

    class FakeTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeConnection:
        closed = False

        async def fetchval(self, query: str, *args: object) -> object:
            if query == "SELECT current_database()":
                return "postgres"
            if query.startswith("SELECT quote_ident"):
                return f'"{args[0]}"'
            if query.startswith("SELECT quote_literal"):
                return "'synthetic password/?'"
            if query.startswith("SELECT 1 FROM pg_roles"):
                return None
            raise AssertionError(f"unexpected query: {query}")

        def transaction(self) -> FakeTransaction:
            return FakeTransaction()

        async def execute(self, query: str) -> None:
            executed.append(query)

        async def close(self) -> None:
            self.closed = True

    connection = FakeConnection()

    async def fake_connect(
        _dsn: str,
        *,
        server_settings: dict[str, str],
    ) -> FakeConnection:
        assert server_settings == {"application_name": "load-test-monitor-provisioner"}
        return connection

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    monitor_dsn = asyncio.run(
        provision_monitor_role(
            "postgresql://provisioner:secret@localhost:5532/postgres"
            "?sslmode=disable"
            "&password=provisioning-query-secret"
            "&USER=provisioning-query-user"
            "&application_name=scatter-provisioner"
            "#provisioning-fragment-secret",
            "00000000-0000-4000-8000-000000000000",
            "scatter_load_monitor",
            "synthetic password/?",
        )
    )

    assert monitor_dsn == (
        "postgresql://scatter_load_monitor:synthetic%20password%2F%3F"
        "@localhost:5532/postgres"
        "?sslmode=disable&application_name=scatter-provisioner"
    )
    assert "provisioning-query-secret" not in monitor_dsn
    assert "provisioning-query-user" not in monitor_dsn
    assert "provisioning-fragment-secret" not in monitor_dsn
    assert executed[0] == (
        'CREATE SCHEMA IF NOT EXISTS "tables_ws_000000001vGeH72LxVtxKg"'
    )
    assert executed[1].startswith('CREATE ROLE "scatter_load_monitor"')
    assert 'GRANT pg_read_all_stats TO "scatter_load_monitor"' in executed
    assert any("GRANT SELECT ON ALL TABLES IN SCHEMA" in sql for sql in executed)
    assert any("ALTER DEFAULT PRIVILEGES IN SCHEMA" in sql for sql in executed)
    assert connection.closed


def test_monitor_provisioning_refuses_to_alter_an_existing_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        closed = False

        async def fetchval(self, query: str, *args: object) -> object:
            if query == "SELECT current_database()":
                return "postgres"
            if query.startswith("SELECT quote_ident"):
                return f'"{args[0]}"'
            if query.startswith("SELECT quote_literal"):
                return "'synthetic-password'"
            if query.startswith("SELECT 1 FROM pg_roles"):
                return 1
            raise AssertionError(f"unexpected query: {query}")

        def transaction(self) -> object:
            raise AssertionError("existing roles must be rejected before mutation")

        async def execute(self, query: str) -> None:
            raise AssertionError(f"unexpected mutation: {query}")

        async def close(self) -> None:
            self.closed = True

    connection = FakeConnection()

    async def fake_connect(
        _dsn: str,
        *,
        server_settings: dict[str, str],
    ) -> FakeConnection:
        assert server_settings == {"application_name": "load-test-monitor-provisioner"}
        return connection

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    with pytest.raises(
        provision_monitor_module.MonitorProvisioningError,
        match="refusing to alter an existing PostgreSQL role",
    ):
        asyncio.run(
            provision_monitor_role(
                "postgresql://provisioner:secret@localhost:5532/postgres",
                "00000000-0000-4000-8000-000000000000",
                "shared_role",
                "replacement-password",
            )
        )

    assert connection.closed


def test_row_correctness_sets_transaction_local_workspace_rls_context() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeTransaction:
        active = False

        async def __aenter__(self) -> None:
            self.active = True

        async def __aexit__(self, *_args: object) -> None:
            self.active = False

    transaction = FakeTransaction()

    class FakeConnection:
        def transaction(self) -> FakeTransaction:
            return transaction

        async def execute(self, query: str, *args: object) -> None:
            assert transaction.active
            calls.append((query, args))

        async def fetchval(self, query: str, *args: object) -> bool:
            assert transaction.active
            calls.append((query, args))
            return True

        async def fetchrow(self, query: str, *args: object) -> dict[str, int] | None:
            assert transaction.active
            calls.append((query, args))
            if "WHERE run_id = $1" in query:
                return {"total": 3, "distinct_keys": 3}
            return {"total": 5, "distinct_keys": 4}

    sampler = PgSampler("postgresql://monitor@localhost/tracecat", ())
    sampler._conn = cast(asyncpg.Connection, FakeConnection())

    correctness = asyncio.run(
        sampler.row_correctness(
            "ws_0000000000000000000000",
            "scatter_load_rows",
            "scatter-test",
        )
    )

    assert calls[0] == (
        WORKSPACE_RLS_CONTEXT_SQL,
        ("00000000-0000-0000-0000-000000000000",),
    )
    assert correctness is not None
    assert correctness["total_rows"] == 5
    assert correctness["distinct_dedupe_keys"] == 4
    assert correctness["rows_for_run"] == 3
    assert correctness["distinct_dedupe_keys_for_run"] == 3
    assert not transaction.active


def test_monitor_provisioning_cli_requires_explicit_dsn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(DEFAULT_PROVISION_DSN_ENV, raising=False)

    exit_code = asyncio.run(
        provision_monitor_module.amain(
            ["--workspace-id", "00000000-0000-4000-8000-000000000000"]
        )
    )

    assert exit_code == 2
    assert DEFAULT_PROVISION_DSN_ENV in capsys.readouterr().err


def test_failed_status_refresh_is_throttled() -> None:
    async def exercise() -> None:
        refresh_count = 0
        requested_limits: list[str | None] = []
        requested_workflow_ids: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal refresh_count
            refresh_count += 1
            requested_limits.append(request.url.params.get("limit"))
            requested_workflow_ids.append(request.url.params.get("workflow_id"))
            return httpx.Response(503)

        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        client._status_cache = {
            "exec-1": {
                "status": "COMPLETED",
                "history_length": 12,
            }
        }
        try:
            with pytest.raises(ExecutionStatusRefreshError):
                await client.get_execution_status("workspace-1", "workflow-1", "exec-1")
            with pytest.raises(ExecutionStatusRefreshError):
                await client.get_execution_status("workspace-1", "workflow-1", "exec-1")
        finally:
            await client.aclose()

        assert refresh_count == 1
        assert requested_limits == ["1000"]
        assert requested_workflow_ids == ["workflow-1"]

    asyncio.run(exercise())


def test_execution_status_refresh_uses_configured_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        clock = 0.0
        refresh_count = 0

        def monotonic() -> float:
            return clock

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal refresh_count
            refresh_count += 1
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "exec-1",
                            "status": "RUNNING",
                            "history_length": 1,
                        }
                    ],
                    "next_cursor": None,
                },
            )

        monkeypatch.setattr(
            "tracecat_benchmark.client.time.monotonic",
            monotonic,
        )
        client = TracecatClient(
            "http://tracecat.test/api",
            execution_poll_interval_seconds=0.25,
        )
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.get_execution_status("workspace-1", "workflow-1", "exec-1")
            clock = 0.249
            await client.get_execution_status("workspace-1", "workflow-1", "exec-1")
            clock = 0.25
            await client.get_execution_status("workspace-1", "workflow-1", "exec-1")
        finally:
            await client.aclose()

        assert refresh_count == 2

    asyncio.run(exercise())


def test_execution_status_refresh_pages_past_snapshot_limit() -> None:
    async def exercise() -> None:
        requested_cursors: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            requested_cursors.append(cursor)
            if cursor is None:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "exec-1",
                                "status": "RUNNING",
                                "history_length": 1,
                            }
                        ],
                        "next_cursor": "page-2",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "exec-2",
                            "status": "COMPLETED",
                            "history_length": 12,
                        }
                    ],
                    "next_cursor": None,
                },
            )

        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            first = await client.get_execution_status(
                "workspace-1", "workflow-1", "exec-1"
            )
            second = await client.get_execution_status(
                "workspace-1", "workflow-1", "exec-2"
            )
        finally:
            await client.aclose()

        assert first is not None
        assert first["status"] == "RUNNING"
        assert second is not None
        assert second["status"] == "COMPLETED"
        assert requested_cursors == [None, "page-2"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"wf_exec_id": None},
        {"wf_exec_id": 123},
        {"wf_exec_id": ""},
        {"wf_exec_id": "   "},
    ],
)
def test_successful_submission_requires_a_nonempty_execution_id(
    payload: dict[str, object],
) -> None:
    async def exercise() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json=payload)

        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await client.submit_execution(
                "workspace-1",
                "workflow-1",
                {},
            )
        finally:
            await client.aclose()

        assert result == {"status_code": 202, "wf_exec_id": None}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([], False),
        ([{"id": "workflow-1/execution-1", "status": "RUNNING"}], True),
    ],
)
def test_running_execution_check_uses_filtered_search(
    items: list[dict[str, str]], expected: bool
) -> None:
    async def exercise() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == (
                "/api/workspaces/workspace-1/workflow-executions/search"
            )
            assert request.url.params["workflow_id"] == "workflow-1"
            assert request.url.params["status"] == "RUNNING"
            assert request.url.params["limit"] == "1"
            return httpx.Response(200, json={"items": items})

        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            assert (
                await client.has_running_executions("workspace-1", "workflow-1")
                is expected
            )
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_execution_snapshot_retains_event_count() -> None:
    async def exercise() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "exec-1",
                            "status": "COMPLETED",
                            "history_length": 321,
                        }
                    ],
                    "next_cursor": None,
                },
            )

        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            snapshot = await client.get_execution_status(
                "workspace-1", "workflow-1", "exec-1"
            )
        finally:
            await client.aclose()

        assert snapshot == {
            "status": "COMPLETED",
            "history_length": 321,
        }

    asyncio.run(exercise())


def test_execution_failure_diagnostics_exclude_free_form_messages() -> None:
    async def exercise() -> list[dict[str, object]]:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "action_ref": "write_rows",
                            "action_name": "core.table.insert_rows",
                            "curr_event_type": "ACTIVITY_TASK_FAILED",
                            "status": "FAILED",
                            "action_error": {
                                "message": "synthetic database error",
                                "root_cause_message": "synthetic pool timeout",
                            },
                            "loop_index": 3,
                        },
                        {
                            "action_ref": "completed_action",
                            "action_name": "core.transform.reshape",
                            "curr_event_type": "ACTIVITY_TASK_COMPLETED",
                            "status": "COMPLETED",
                        },
                    ]
                },
            )

        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            diagnostics = await client.get_execution_failure_diagnostics(
                "workspace-1",
                "exec-1",
            )
        finally:
            await client.aclose()
        return cast(list[dict[str, object]], diagnostics)

    assert asyncio.run(exercise()) == [
        {
            "action_ref": "write_rows",
            "action_name": "core.table.insert_rows",
            "event_type": "ACTIVITY_TASK_FAILED",
            "status": "FAILED",
            "child_wf_exec_id": None,
            "loop_index": 3,
        }
    ]


def test_execution_latency_includes_admission_time() -> None:
    record = ExecutionRecord(
        workflow_seq=1,
        phase=Phase.RAMP,
        submitted_at=10.0,
        accepted_at=12.0,
        terminal_at=15.0,
    )

    assert record.latency_seconds == 5.0


def test_submission_is_bounded_by_the_run_timeout(tmp_path: Path) -> None:
    class StalledAdmissionClient:
        async def submit_execution(
            self,
            _workspace_id: str,
            _workflow_id: str,
            _inputs: dict[str, object],
        ) -> None:
            await asyncio.Event().wait()

    async def exercise() -> tuple[LoadRunner, ExecutionRecord]:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, StalledAdmissionClient()),
            replace(
                _scenario_config(
                    tmp_path,
                    warmup=False,
                    workflow_count=1,
                    one_shot=True,
                ),
                run_timeout_seconds=0.01,
            ),
            "workflow-1",
            writer,
        )
        try:
            record = await asyncio.wait_for(
                runner._run_one(Phase.RAMP, branch_count=1),
                timeout=0.5,
            )
        finally:
            writer.close()
        return runner, record

    runner, record = asyncio.run(exercise())

    assert record.failure_mode is FailureMode.SUBMIT_TIMEOUT
    assert record.submit_status_code is None
    assert record.wf_exec_id is None
    assert record.terminal_at is not None
    assert runner._may_still_be_running(record)


@pytest.mark.parametrize(
    ("status_code", "expected_failure", "may_still_be_running"),
    [
        (202, FailureMode.ADMISSION_OUTCOME_UNKNOWN, True),
        (429, FailureMode.ADMISSION_REJECTED, False),
    ],
)
def test_submission_without_execution_id_retires_ambiguous_2xx_slot(
    tmp_path: Path,
    status_code: int,
    expected_failure: FailureMode,
    may_still_be_running: bool,
) -> None:
    class MissingExecutionIdClient:
        async def submit_execution(
            self,
            _workspace_id: str,
            _workflow_id: str,
            _inputs: dict[str, object],
        ) -> dict[str, int | None]:
            return {"status_code": status_code, "wf_exec_id": None}

    async def exercise() -> tuple[LoadRunner, ExecutionRecord]:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, MissingExecutionIdClient()),
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=False,
            ),
            "workflow-1",
            writer,
        )
        try:
            record = await runner._run_one(Phase.RAMP, branch_count=1)
        finally:
            writer.close()
        return runner, record

    runner, record = asyncio.run(exercise())

    assert record.submit_status_code == status_code
    assert record.wf_exec_id is None
    assert record.failure_mode is expected_failure
    assert runner._may_still_be_running(record) is may_still_be_running


def test_runner_records_terminal_history_length(tmp_path: Path) -> None:
    class FakeClient:
        async def get_execution_status(
            self, _workspace_id: str, _workflow_id: str, _wf_exec_id: str
        ) -> dict[str, str | int]:
            return {
                "status": "COMPLETED",
                "history_length": 123,
            }

    async def exercise() -> None:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, FakeClient()),
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=True,
            ),
            "workflow-1",
            writer,
        )
        record = ExecutionRecord(
            workflow_seq=1,
            phase=Phase.RAMP,
            submitted_at=time.monotonic(),
            wf_exec_id="exec-1",
        )
        try:
            await runner._poll_to_terminal(record)
        finally:
            writer.close()

        assert record.terminal_status == "COMPLETED"
        assert record.history_length == 123

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("first_poll_succeeds", "expected_failure", "expected_status"),
    [
        (True, FailureMode.RUN_TIMEOUT, "RUNNING"),
        (False, FailureMode.POLL_TRANSPORT_ERROR, None),
    ],
)
def test_poll_timeout_classification_uses_polling_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_poll_succeeds: bool,
    expected_failure: FailureMode,
    expected_status: str | None,
) -> None:
    async def exercise() -> ExecutionRecord:
        clock = 0.0

        class HistoryClient:
            calls = 0

            async def get_execution_status(
                self,
                _workspace_id: str,
                _workflow_id: str,
                _wf_exec_id: str,
            ) -> dict[str, str | int]:
                nonlocal clock
                self.calls += 1
                clock = 0.5 if self.calls == 1 else 1.0
                if first_poll_succeeds and self.calls == 1:
                    return {
                        "status": "RUNNING",
                        "history_length": 1,
                    }
                raise ExecutionStatusRefreshError("synthetic polling outage")

        monkeypatch.setattr(
            "tracecat_benchmark.runner.time.monotonic",
            lambda: clock,
        )
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, HistoryClient()),
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=True,
            ),
            "workflow-1",
            writer,
        )
        record = ExecutionRecord(
            workflow_seq=1,
            phase=Phase.RAMP,
            submitted_at=0.0,
            accepted_at=0.0,
            wf_exec_id="exec-1",
        )
        try:
            await runner._poll_to_terminal(record)
        finally:
            writer.close()
        return record

    record = asyncio.run(exercise())

    assert record.failure_mode is expected_failure
    assert record.terminal_status == expected_status
    assert record.poll_count == 2


def test_execution_polling_subtracts_request_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> list[float]:
        clock = 0.0
        sleeps: list[float] = []

        class SlowPollClient:
            calls = 0

            async def get_execution_status(
                self,
                _workspace_id: str,
                _workflow_id: str,
                _wf_exec_id: str,
            ) -> dict[str, str | int]:
                nonlocal clock
                self.calls += 1
                clock += 0.4
                return {
                    "status": "RUNNING" if self.calls == 1 else "COMPLETED",
                    "history_length": self.calls,
                }

        async def advance_clock(delay: float) -> None:
            nonlocal clock
            sleeps.append(delay)
            clock += delay

        monkeypatch.setattr(
            "tracecat_benchmark.runner.time.monotonic",
            lambda: clock,
        )
        monkeypatch.setattr(
            "tracecat_benchmark.runner.asyncio.sleep",
            advance_clock,
        )
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, SlowPollClient()),
            replace(
                _scenario_config(
                    tmp_path,
                    warmup=False,
                    workflow_count=1,
                    one_shot=True,
                ),
                poll_interval_seconds=1.0,
                run_timeout_seconds=10.0,
            ),
            "workflow-1",
            writer,
        )
        record = ExecutionRecord(
            workflow_seq=1,
            phase=Phase.RAMP,
            submitted_at=0.0,
            accepted_at=0.0,
            wf_exec_id="exec-1",
        )
        try:
            await runner._poll_to_terminal(record)
        finally:
            writer.close()

        assert record.terminal_status == "COMPLETED"
        return sleeps

    assert asyncio.run(exercise()) == pytest.approx([1.0, 0.6])


def test_execution_timeout_prevents_late_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> tuple[ExecutionRecord, int, list[float]]:
        clock = 0.0
        sleeps: list[float] = []

        class UncalledClient:
            calls = 0

            async def get_execution_status(
                self,
                _workspace_id: str,
                _workflow_id: str,
                _wf_exec_id: str,
            ) -> dict[str, str | int]:
                self.calls += 1
                return {
                    "status": "COMPLETED",
                    "history_length": 1,
                }

        async def advance_clock(delay: float) -> None:
            nonlocal clock
            sleeps.append(delay)
            clock += delay

        client = UncalledClient()
        monkeypatch.setattr(
            "tracecat_benchmark.runner.time.monotonic",
            lambda: clock,
        )
        monkeypatch.setattr(
            "tracecat_benchmark.runner.asyncio.sleep",
            advance_clock,
        )
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, client),
            replace(
                _scenario_config(
                    tmp_path,
                    warmup=False,
                    workflow_count=1,
                    one_shot=True,
                ),
                poll_interval_seconds=1.0,
                run_timeout_seconds=0.1,
            ),
            "workflow-1",
            writer,
        )
        record = ExecutionRecord(
            workflow_seq=1,
            phase=Phase.RAMP,
            submitted_at=0.0,
            accepted_at=0.0,
            wf_exec_id="exec-1",
        )
        try:
            await runner._poll_to_terminal(record)
        finally:
            writer.close()
        return record, client.calls, sleeps

    record, calls, sleeps = asyncio.run(exercise())

    assert record.failure_mode is FailureMode.RUN_TIMEOUT
    assert record.terminal_status is None
    assert calls == 0
    assert sleeps == pytest.approx([0.1])


def test_execution_response_after_deadline_is_timed_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> ExecutionRecord:
        clock = 0.0

        class SlowTerminalClient:
            async def get_execution_status(
                self,
                _workspace_id: str,
                _workflow_id: str,
                _wf_exec_id: str,
            ) -> dict[str, str | int]:
                nonlocal clock
                clock += 0.11
                return {
                    "status": "COMPLETED",
                    "history_length": 1,
                }

        async def advance_clock(delay: float) -> None:
            nonlocal clock
            clock += delay

        monkeypatch.setattr(
            "tracecat_benchmark.runner.time.monotonic",
            lambda: clock,
        )
        monkeypatch.setattr(
            "tracecat_benchmark.runner.asyncio.sleep",
            advance_clock,
        )
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, SlowTerminalClient()),
            replace(
                _scenario_config(
                    tmp_path,
                    warmup=False,
                    workflow_count=1,
                    one_shot=True,
                ),
                poll_interval_seconds=0.1,
                run_timeout_seconds=0.2,
            ),
            "workflow-1",
            writer,
        )
        record = ExecutionRecord(
            workflow_seq=1,
            phase=Phase.RAMP,
            submitted_at=0.0,
            accepted_at=0.0,
            wf_exec_id="exec-1",
        )
        try:
            await runner._poll_to_terminal(record)
        finally:
            writer.close()
        return record

    record = asyncio.run(exercise())

    assert record.failure_mode is FailureMode.RUN_TIMEOUT
    assert record.terminal_status is None
    assert record.poll_count == 1


def _scenario_config(
    artifact_dir: Path,
    *,
    warmup: bool,
    workflow_count: int,
    one_shot: bool,
) -> ScenarioConfig:
    return ScenarioConfig(
        run_id="scatter-test",
        base_url="http://tracecat.test/api",
        cluster_num=1,
        workspace_id="workspace-1",
        load_type=LoadType.SCATTER,
        workflow_count=workflow_count,
        branch_count=1,
        ramp_seconds=0.0,
        steady_state_seconds=0.0,
        payload_bytes=1,
        run_timeout_seconds=1.0,
        poll_interval_seconds=0.0,
        warmup=warmup,
        one_shot=one_shot,
        collector_ready_timeout_seconds=1.0,
        submit_concurrency=workflow_count,
        max_connections=1,
        artifact_dir=str(artifact_dir),
        tracecat_commit="test-commit",
        started_at="2026-07-29T00:00:00+00:00",
        auth_mode="api_key",
        abort_stops_polling=False,
        case_id="unit-scatter",
    )


def test_measured_timing_starts_after_warmup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0
    monkeypatch.setattr(
        "tracecat_benchmark.runner.time.monotonic",
        lambda: clock,
    )

    class WarmupTimingRunner(LoadRunner):
        async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
            nonlocal clock
            assert phase is Phase.WARMUP
            assert branch_count > 0
            clock = 10.0
            return ExecutionRecord(
                workflow_seq=0,
                phase=phase,
                submitted_at=0.0,
                terminal_at=clock,
            )

    async def exercise() -> None:
        client = TracecatClient("http://tracecat.test/api")
        scenario = _scenario_config(
            tmp_path,
            warmup=True,
            workflow_count=0,
            one_shot=False,
        )
        runner = WarmupTimingRunner(
            client,
            scenario,
            "workflow-1",
            JsonLinesWriter(tmp_path / "runner.jsonl"),
        )
        try:
            summary = await runner.run()
        finally:
            await client.aclose()

        assert summary.wall_clock_seconds == 0.0
        assert summary.first_failure_at is None

    asyncio.run(exercise())


def test_unresolved_warmup_aborts_before_measured_load(tmp_path: Path) -> None:
    measured_worker_started = False

    class UnresolvedWarmupRunner(LoadRunner):
        async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
            assert phase is Phase.WARMUP
            assert branch_count > 0
            return ExecutionRecord(
                workflow_seq=0,
                phase=phase,
                submitted_at=0.0,
                accepted_at=0.0,
                terminal_at=1.0,
                wf_exec_id="exec-warmup",
                terminal_status="RUNNING",
                failure_mode=FailureMode.RUN_TIMEOUT,
            )

        async def _worker(self, stagger: float, sustain_deadline: float) -> None:
            nonlocal measured_worker_started
            measured_worker_started = True

    async def exercise() -> None:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = UnresolvedWarmupRunner(
            cast(TracecatClient, object()),
            _scenario_config(
                tmp_path,
                warmup=True,
                workflow_count=1,
                one_shot=False,
            ),
            "workflow-1",
            writer,
        )
        try:
            with pytest.raises(
                WarmupIsolationError,
                match="warm-up did not reach a terminal state",
            ):
                await runner.run()
        finally:
            writer.close()

    asyncio.run(exercise())
    assert not measured_worker_started


def test_sustained_summary_targets_actual_submissions(tmp_path: Path) -> None:
    writer = JsonLinesWriter(tmp_path / "runner.jsonl")
    runner = LoadRunner(
        cast(TracecatClient, object()),
        _scenario_config(
            tmp_path,
            warmup=False,
            workflow_count=1,
            one_shot=False,
        ),
        "workflow-1",
        writer,
    )
    runner._started_monotonic = time.monotonic()
    runner._records = [
        ExecutionRecord(
            workflow_seq=sequence,
            phase=Phase.SUSTAIN,
            submitted_at=runner._started_monotonic,
            accepted_at=runner._started_monotonic,
            terminal_at=runner._started_monotonic,
            terminal_status="COMPLETED",
        )
        for sequence in range(2)
    ]
    try:
        summary = runner.summarize()
        rendered = render_summary(runner._scenario, summary)
    finally:
        writer.close()

    assert summary.expected_rows == 2
    assert summary.submitted_row_target == 2
    assert summary.run_id == run_id_fingerprint("scatter-test")
    assert "scatter-test" not in rendered
    assert "submitted-row target   2 (if all 2 submitted workflows succeed)" in rendered
    assert "target 1 at full success" not in rendered


def test_runner_only_summary_does_not_claim_row_correctness(tmp_path: Path) -> None:
    writer = JsonLinesWriter(tmp_path / "runner.jsonl")
    runner = LoadRunner(
        cast(TracecatClient, object()),
        replace(
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=True,
            ),
            evidence_mode="runner_only",
        ),
        "workflow-1",
        writer,
    )
    runner._started_monotonic = time.monotonic()
    try:
        rendered = render_summary(runner._scenario, runner.summarize())
    finally:
        writer.close()

    assert "actual row correctness is unavailable in runner-only evidence" in rendered
    assert "actual row counts are recorded by the metric collector" not in rendered


def test_noop_summary_expects_no_fixture_rows(tmp_path: Path) -> None:
    writer = JsonLinesWriter(tmp_path / "runner.jsonl")
    runner = LoadRunner(
        cast(TracecatClient, object()),
        replace(
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=True,
            ),
            load_type=LoadType.NOOP,
            branch_count=256,
        ),
        "workflow-1",
        writer,
    )
    runner._started_monotonic = time.monotonic()
    runner._records = [
        ExecutionRecord(
            workflow_seq=0,
            phase=Phase.RAMP,
            submitted_at=runner._started_monotonic,
            accepted_at=runner._started_monotonic,
            terminal_at=runner._started_monotonic,
            terminal_status="COMPLETED",
        )
    ]
    try:
        summary = runner.summarize()
        rendered = render_summary(runner._scenario, summary)
    finally:
        writer.close()

    assert summary.expected_rows == 0
    assert summary.submitted_row_target == 0
    assert "expected unique rows   0" in rendered


def test_timeout_summary_includes_every_deadline_expiry_mode(tmp_path: Path) -> None:
    writer = JsonLinesWriter(tmp_path / "runner.jsonl")
    runner = LoadRunner(
        cast(TracecatClient, object()),
        _scenario_config(
            tmp_path,
            warmup=False,
            workflow_count=1,
            one_shot=False,
        ),
        "workflow-1",
        writer,
    )
    runner._started_monotonic = time.monotonic()
    runner._records = [
        ExecutionRecord(
            workflow_seq=sequence,
            phase=Phase.SUSTAIN,
            submitted_at=runner._started_monotonic,
            terminal_at=runner._started_monotonic,
            failure_mode=failure_mode,
        )
        for sequence, failure_mode in enumerate(
            (
                FailureMode.SUBMIT_TIMEOUT,
                FailureMode.RUN_TIMEOUT,
                FailureMode.POLL_TRANSPORT_ERROR,
                FailureMode.SUBMIT_TRANSPORT_ERROR,
            )
        )
    ]
    try:
        summary = runner.summarize()
        rendered = render_summary(runner._scenario, summary)
    finally:
        writer.close()

    assert summary.timed_out == 3
    assert "timed out              3" in rendered


def test_one_shot_worker_does_not_replenish_completed_work(tmp_path: Path) -> None:
    class OneShotRunner(LoadRunner):
        calls = 0

        async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
            self.calls += 1
            return ExecutionRecord(
                workflow_seq=self.calls,
                phase=phase,
                submitted_at=0.0,
                terminal_at=0.0,
            )

    async def exercise() -> None:
        client = TracecatClient("http://tracecat.test/api")
        runner = OneShotRunner(
            client,
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=True,
            ),
            "workflow-1",
            JsonLinesWriter(tmp_path / "runner.jsonl"),
        )
        runner._ramp_deadline = float("inf")
        try:
            await runner._worker(0.0, float("inf"))
        finally:
            await client.aclose()

        assert runner.calls == 1

    asyncio.run(exercise())


def test_staggered_worker_wakes_immediately_on_abort(tmp_path: Path) -> None:
    class StaggeredRunner(LoadRunner):
        calls = 0

        async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
            self.calls += 1
            return ExecutionRecord(
                workflow_seq=self.calls,
                phase=phase,
                submitted_at=0.0,
            )

    async def exercise() -> None:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = StaggeredRunner(
            cast(TracecatClient, object()),
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=False,
            ),
            "workflow-1",
            writer,
        )
        runner._ramp_deadline = float("inf")
        worker = asyncio.create_task(runner._worker(60.0, float("inf")))
        try:
            await asyncio.sleep(0)
            runner.request_abort()
            await asyncio.wait_for(worker, timeout=0.1)
        finally:
            writer.close()

        assert runner.calls == 0

    asyncio.run(exercise())


def test_worker_does_not_replenish_timed_out_in_flight_work(
    tmp_path: Path,
) -> None:
    class TimedOutRunner(LoadRunner):
        calls = 0

        async def _run_one(self, phase: Phase, branch_count: int) -> ExecutionRecord:
            self.calls += 1
            return ExecutionRecord(
                workflow_seq=self.calls,
                phase=phase,
                submitted_at=0.0,
                accepted_at=0.0,
                terminal_at=1.0,
                wf_exec_id="exec-1",
                terminal_status="RUNNING",
                failure_mode=FailureMode.RUN_TIMEOUT,
            )

    async def exercise() -> None:
        client = TracecatClient("http://tracecat.test/api")
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = TimedOutRunner(
            client,
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=1,
                one_shot=False,
            ),
            "workflow-1",
            writer,
        )
        runner._ramp_deadline = float("inf")
        try:
            await asyncio.wait_for(
                runner._worker(0.0, float("inf")),
                timeout=0.1,
            )
        finally:
            writer.close()
            await client.aclose()

        assert runner.calls == 1

    asyncio.run(exercise())


def test_failure_diagnostics_are_captured_after_measured_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0

    class DiagnosticClient:
        async def get_execution_failure_diagnostics(
            self,
            _workspace_id: str,
            _wf_exec_id: str,
        ) -> list[dict[str, object]]:
            nonlocal clock
            clock = 5.0
            return [
                {
                    "action_ref": "write_rows",
                    "action_name": "core.table.insert_rows",
                    "event_type": "ACTIVITY_TASK_FAILED",
                    "status": "FAILED",
                    "child_wf_exec_id": None,
                    "loop_index": 0,
                }
            ]

    async def exercise() -> RunSummary:
        monkeypatch.setattr(
            "tracecat_benchmark.runner.time.monotonic",
            lambda: clock,
        )
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, DiagnosticClient()),
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=0,
                one_shot=False,
            ),
            "workflow-1",
            writer,
        )
        runner._records = [
            ExecutionRecord(
                workflow_seq=1,
                phase=Phase.SUSTAIN,
                submitted_at=0.0,
                accepted_at=0.0,
                terminal_at=0.0,
                wf_exec_id="exec-1",
                terminal_status="FAILED",
                failure_mode=FailureMode.WORKFLOW_FAILED,
            )
        ]
        try:
            return await runner.run()
        finally:
            writer.close()

    summary = asyncio.run(exercise())
    records = [
        json.loads(line)
        for line in (tmp_path / "runner.jsonl").read_text().splitlines()
    ]

    assert summary.wall_clock_seconds == 0.0
    assert records == [
        {
            "kind": "execution_failure_diagnostics",
            "workflow_seq": 1,
            "workflow_execution_fingerprint": workflow_execution_fingerprint("exec-1"),
            "terminal_status": "FAILED",
            "diagnostics": [
                {
                    "action_ref": "write_rows",
                    "action_name": "core.table.insert_rows",
                    "event_type": "ACTIVITY_TASK_FAILED",
                    "status": "FAILED",
                    "child_wf_exec_id": None,
                    "loop_index": 0,
                }
            ],
            "capture_error": None,
        }
    ]


@pytest.mark.parametrize(
    ("empty_response", "expected_capture_error"),
    [
        (False, ExecutionStatusRefreshError.__name__),
        (True, MissingFailureDiagnosticsError.__name__),
    ],
)
def test_failure_diagnostics_capture_error_invalidates_run(
    tmp_path: Path,
    empty_response: bool,
    expected_capture_error: str,
) -> None:
    class DiagnosticClient:
        async def get_execution_failure_diagnostics(
            self,
            _workspace_id: str,
            _wf_exec_id: str,
        ) -> list[ExecutionFailureDiagnostic]:
            if empty_response:
                return []
            raise ExecutionStatusRefreshError("synthetic diagnostics outage")

    async def exercise() -> None:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, DiagnosticClient()),
            _scenario_config(
                tmp_path,
                warmup=False,
                workflow_count=0,
                one_shot=False,
            ),
            "workflow-1",
            writer,
        )
        runner._records = [
            ExecutionRecord(
                workflow_seq=1,
                phase=Phase.SUSTAIN,
                submitted_at=0.0,
                accepted_at=0.0,
                terminal_at=0.0,
                wf_exec_id="exec-1",
                terminal_status="FAILED",
                failure_mode=FailureMode.WORKFLOW_FAILED,
            )
        ]
        try:
            with pytest.raises(
                FailureDiagnosticsCaptureError,
                match="required failure diagnostics were unavailable",
            ):
                await runner.run()
        finally:
            writer.close()

    asyncio.run(exercise())

    records = [
        json.loads(line)
        for line in (tmp_path / "runner.jsonl").read_text().splitlines()
    ]
    assert records == [
        {
            "kind": "execution_failure_diagnostics",
            "workflow_seq": 1,
            "workflow_execution_fingerprint": workflow_execution_fingerprint("exec-1"),
            "terminal_status": "FAILED",
            "diagnostics": [],
            "capture_error": expected_capture_error,
        }
    ]


def test_runner_waits_for_matching_collector_readiness(tmp_path: Path) -> None:
    async def exercise() -> None:
        artifact_dir = tmp_path / "scatter-test"
        artifact_dir.mkdir()

        async def publish_ready() -> None:
            await asyncio.sleep(0.01)
            (artifact_dir / "collector_ready.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id_fingerprint("scatter-test"),
                        "status": "ready",
                        "cluster_num": 1,
                        "public_api_url": "http://localhost:180/api",
                        "workspace_fingerprint": workspace_fingerprint(
                            "00000000-0000-4000-8000-000000000000"
                        ),
                        "sample_count": 1,
                        "temporal_sample_count": 1,
                        "resource_sample_count": 1,
                    }
                ),
                encoding="utf-8",
            )

        publisher = asyncio.create_task(publish_ready())
        await _wait_for_collector_ready(
            artifact_dir,
            "scatter-test",
            1.0,
            cluster_num=1,
            public_api_url="http://localhost:180/api",
            workspace_id="00000000-0000-4000-8000-000000000000",
        )
        await publisher

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("cluster_num", "public_api_url", "workspace_id", "mismatched_field"),
    [
        (
            2,
            "http://localhost:180/api",
            "00000000-0000-4000-8000-000000000000",
            "cluster_num",
        ),
        (
            1,
            "http://localhost:280/api",
            "00000000-0000-4000-8000-000000000000",
            "public_api_url",
        ),
        (
            1,
            "http://localhost:180/api",
            "00000000-0000-4000-8000-000000000001",
            "workspace_fingerprint",
        ),
    ],
)
def test_runner_rejects_collector_readiness_for_a_different_target(
    tmp_path: Path,
    cluster_num: int,
    public_api_url: str,
    workspace_id: str,
    mismatched_field: str,
) -> None:
    artifact_dir = tmp_path / "scatter-test"
    artifact_dir.mkdir()
    (artifact_dir / "collector_ready.json").write_text(
        json.dumps(
            {
                "run_id": run_id_fingerprint("scatter-test"),
                "status": "ready",
                "cluster_num": 1,
                "public_api_url": "http://localhost:180/api",
                "workspace_fingerprint": workspace_fingerprint(
                    "00000000-0000-4000-8000-000000000000"
                ),
                "sample_count": 1,
                "temporal_sample_count": 1,
                "resource_sample_count": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CollectorReadinessError, match=mismatched_field):
        asyncio.run(
            _wait_for_collector_ready(
                artifact_dir,
                "scatter-test",
                1.0,
                cluster_num=cluster_num,
                public_api_url=public_api_url,
                workspace_id=workspace_id,
            )
        )


def test_runner_rejects_existing_artifacts_for_reused_run_id(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact_dir = tmp_path / run_id_fingerprint("scatter-test")
    artifact_dir.mkdir()
    (artifact_dir / "collector_ready.json").write_text(
        json.dumps(
            {
                "run_id": run_id_fingerprint("scatter-test"),
                "status": "ready",
                "cluster_num": 1,
                "public_api_url": "http://localhost/api",
                "workspace_fingerprint": workspace_fingerprint(
                    "00000000-0000-4000-8000-000000000000"
                ),
                "sample_count": 1,
                "temporal_sample_count": 1,
                "resource_sample_count": 1,
            }
        ),
        encoding="utf-8",
    )
    existing_record = '{"kind":"existing"}\n'
    (artifact_dir / "runner_results.jsonl").write_text(
        existing_record,
        encoding="utf-8",
    )

    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--cluster-num",
                "1",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--run-id",
                "scatter-test",
                "--artifact-root",
                str(tmp_path),
            ]
        )
    )

    assert exit_code == 2
    assert "refusing to overwrite or append" in capsys.readouterr().err
    assert (artifact_dir / "runner_results.jsonl").read_text() == existing_record


def test_startup_signal_publishes_aborted_runner_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    callbacks: dict[signal.Signals, object] = {}
    removed_signals: list[signal.Signals] = []

    async def interrupt_collector_wait(*_args: object, **_kwargs: object) -> None:
        callback = callbacks[signal.SIGTERM]
        assert callable(callback)
        callback()
        await asyncio.sleep(0)

    async def exercise() -> int:
        loop = asyncio.get_running_loop()

        def add_signal_handler(
            sig: signal.Signals, callback: object, *_args: object
        ) -> None:
            callbacks[sig] = callback

        def remove_signal_handler(sig: signal.Signals) -> bool:
            removed_signals.append(sig)
            return True

        monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
        monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)
        monkeypatch.setattr(
            runner_module,
            "_wait_for_collector_ready",
            interrupt_collector_wait,
        )
        return await runner_module.amain(
            [
                "--base-url",
                "http://localhost:180/api",
                "--cluster-num",
                "1",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--run-id",
                "scatter-test",
                "--artifact-root",
                str(tmp_path),
            ]
        )

    exit_code = asyncio.run(exercise())

    assert exit_code == 1
    assert set(callbacks) == {signal.SIGINT, signal.SIGTERM}
    assert set(removed_signals) == {signal.SIGINT, signal.SIGTERM}
    artifact_dir = tmp_path / run_id_fingerprint("scatter-test")
    completion = json.loads(
        (artifact_dir / RUNNER_COMPLETE_FILENAME).read_text(encoding="utf-8")
    )
    assert completion["status"] == "aborted"
    assert completion["run_id"] == run_id_fingerprint("scatter-test")
    assert "aborted by signal during startup" in capsys.readouterr().err


def test_active_query_age_excludes_idle_sessions() -> None:
    assert (
        "max(EXTRACT(EPOCH FROM (now() - query_start)))\n"
        "        FILTER (WHERE state = 'active')" in ACTIVITY_SQL
    )
    assert "WHERE state = 'active' AND wait_event IS NOT NULL" in ACTIVITY_SQL


def test_temporal_sampler_captures_workflow_and_activity_backlog() -> None:
    class FakeWorkflowService:
        def __init__(self) -> None:
            self.requests: list[DescribeTaskQueueRequest] = []

        async def describe_task_queue(
            self, request: DescribeTaskQueueRequest
        ) -> DescribeTaskQueueResponse:
            self.requests.append(request)
            return DescribeTaskQueueResponse(
                stats=TaskQueueStats(
                    approximate_backlog_count=7,
                    approximate_backlog_age=Duration(seconds=2, nanos=500_000_000),
                    tasks_add_rate=4.0,
                    tasks_dispatch_rate=3.0,
                )
            )

    class FakeTemporalClient:
        def __init__(self) -> None:
            self.workflow_service = FakeWorkflowService()

    async def exercise() -> None:
        client = FakeTemporalClient()
        sampler = TemporalSampler(
            "localhost:7233",
            "default",
            ("tracecat-task-queue",),
            ("tracecat-task-queue", "shared-action-queue"),
        )
        sampler._client = cast(TemporalClient, client)

        sample = await sampler.sample()

        assert len(client.workflow_service.requests) == 3
        workflow_queue_key = deployment_value_fingerprint("tracecat-task-queue")
        activity_queue_key = deployment_value_fingerprint("shared-action-queue")
        assert sample["workflow_task_queues"][workflow_queue_key] == {
            "approximate_backlog_count": 7,
            "approximate_backlog_age_seconds": 2.5,
            "tasks_add_rate": 4.0,
            "tasks_dispatch_rate": 3.0,
        }
        assert (
            sample["activity_task_queues"][workflow_queue_key][
                "approximate_backlog_count"
            ]
            == 7
        )
        assert (
            sample["activity_task_queues"][activity_queue_key][
                "approximate_backlog_count"
            ]
            == 7
        )

    asyncio.run(exercise())


def test_resource_sampler_captures_container_and_host_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del timeout, env
        commands.append(args)
        if args[1] == "ps":
            return 0, "container-1\tpostgres_db\n", ""
        return (
            0,
            json.dumps(
                {
                    "ID": "container-1",
                    "Name": "tracecat-affected-customer-postgres_db-1",
                    "CPUPerc": "12.50%",
                    "MemUsage": "256MiB / 1GiB",
                    "MemPerc": "25.00%",
                    "NetIO": "1kB / 2kB",
                    "BlockIO": "3MB / 4MB",
                    "PIDs": "6",
                }
            )
            + "\n",
            "",
        )

    host = HostResourceUsage(
        logical_cpu_count=8,
        load_average_1m=1.0,
        load_average_5m=0.75,
        load_average_15m=0.5,
        memory_total_bytes=16_000_000_000,
        memory_available_bytes=8_000_000_000,
        memory_used_percent=50.0,
    )
    monkeypatch.setattr(collector_module, "_run_command", fake_run_command)
    monkeypatch.setattr(collector_module, "_host_resource_usage", lambda: host)

    sample = asyncio.run(DockerResourceSampler("tracecat-test").sample())

    assert commands[0][-2:] == [
        "--format",
        '{{.ID}}\t{{.Label "com.docker.compose.service"}}',
    ]
    assert "label=com.docker.compose.project=tracecat-test" in commands[0]
    assert commands[1][-1] == "container-1"
    assert sample["host"]["memory_used_percent"] == 50.0
    assert sample["containers"] == [
        {
            "container_id": "container-1",
            "service": "postgres_db",
            "cpu_percent": 12.5,
            "memory_usage_bytes": 268_435_456,
            "memory_limit_bytes": 1_073_741_824,
            "memory_percent": 25.0,
            "network_input_bytes": 1000,
            "network_output_bytes": 2000,
            "block_read_bytes": 3_000_000,
            "block_write_bytes": 4_000_000,
            "pids": 6,
        }
    ]


def test_compose_config_redacts_sensitive_environment_values() -> None:
    rendered = """
name: tracecat-affected-customer-1
networks:
  default:
    name: tracecat-affected-customer-1_default
volumes:
  core-db:
    name: tracecat-affected-customer-1_core-db
services:
  api:
    container_name: affected-customer-api
    image: private.affected-customer.test/tracecat:affected-customer-tag
    build:
      context: /Users/affected-customer/repo
      dockerfile: /Users/affected-customer/repo/Dockerfile
      args:
        NEXT_PUBLIC_API_URL: sensitive-build-api-url
        NEXT_PUBLIC_APP_URL: sensitive-build-app-url
        NODE_ENV: development
    env_file:
      - /Users/affected-customer/repo/.env
    volumes:
      - /Users/affected-customer/repo/scripts:/app/scripts:ro
      - /Users/affected-customer/external:/external:ro
    environment:
      TRACECAT__DB_URI: sensitive-mapping-test-value
      TRACECAT__AUTH_SUPERADMIN_EMAIL: sensitive-email-test-value
      OIDC_CLIENT_ID: sensitive-client-id-test-value
      OIDC_ISSUER: sensitive-issuer-test-value
      SAML_IDP_METADATA_URL: sensitive-metadata-url-test-value
      AWS_ACCOUNT_ID: sensitive-account-test-value
      TRACECAT__ALLOW_ORIGINS: sensitive-origin-list-test-value
      TEMPORAL__CLUSTER_NAMESPACE: sensitive-namespace-test-value
      TEMPORAL__CLUSTER_QUEUE: sensitive-queue-test-value
      TRACECAT__AUTH_MIN_PASSWORD_LENGTH: "12"
  worker:
    environment:
      - OAUTH_CLIENT_SECRET=sensitive-list-test-value
      - PUBLIC_URL=https://tracecat.test
      - TEMPORAL_CORS_ORIGINS=sensitive-temporal-origin-test-value
"""

    redacted = _redact_compose_config(
        rendered,
        repo_root=Path("/Users/affected-customer/repo"),
    )

    assert "tracecat-affected-customer-1" not in redacted
    assert "affected-customer-api" not in redacted
    assert "private.affected-customer.test" not in redacted
    assert "affected-customer-tag" not in redacted
    assert "/Users/affected-customer" not in redacted
    assert "<repo>/Dockerfile" in redacted
    assert "<repo>/scripts:/app/scripts:ro" in redacted
    assert REDACTED_PATH_VALUE in redacted
    assert compose_project_fingerprint("tracecat-affected-customer-1") in redacted
    assert (
        deployment_value_fingerprint("tracecat-affected-customer-1_default") in redacted
    )
    assert (
        deployment_value_fingerprint("tracecat-affected-customer-1_core-db") in redacted
    )
    assert (
        deployment_value_fingerprint(
            "private.affected-customer.test/tracecat:affected-customer-tag"
        )
        in redacted
    )
    assert "sensitive-mapping-test-value" not in redacted
    assert "sensitive-list-test-value" not in redacted
    assert "sensitive-email-test-value" not in redacted
    assert "sensitive-client-id-test-value" not in redacted
    assert "sensitive-issuer-test-value" not in redacted
    assert "sensitive-metadata-url-test-value" not in redacted
    assert "sensitive-account-test-value" not in redacted
    assert "sensitive-origin-list-test-value" not in redacted
    assert "sensitive-namespace-test-value" not in redacted
    assert "sensitive-queue-test-value" not in redacted
    assert "sensitive-temporal-origin-test-value" not in redacted
    assert "sensitive-build-api-url" not in redacted
    assert "sensitive-build-app-url" not in redacted
    assert "https://tracecat.test" not in redacted
    assert redacted.count(REDACTED_ENV_VALUE) == 15
    assert "TRACECAT__AUTH_MIN_PASSWORD_LENGTH: '12'" in redacted
    assert "NODE_ENV: development" in redacted


def _collector_config(
    artifact_dir: Path, *, log_services: tuple[str, ...] = ()
) -> CollectorConfig:
    return CollectorConfig(
        run_id="scatter-test",
        workspace_id="00000000-0000-4000-8000-000000000000",
        artifact_dir=str(artifact_dir),
        dsn="postgresql://monitor@localhost/tracecat",
        sample_interval_seconds=0.5,
        readiness_timeout_seconds=1.0,
        cluster_num=1,
        public_api_url="http://localhost:80/api",
        compose_public_app_url="http://localhost:80",
        compose_public_api_url="http://localhost:80/api",
        ee_multi_tenant=True,
        compose_project="tracecat-test-1",
        compose_files=("docker-compose.dev.yml",),
        log_services=log_services,
        recovery_seconds=0.01,
        temporal_target="localhost:7233",
        temporal_namespace="default",
        temporal_workflow_task_queues=("tracecat-task-queue",),
        temporal_activity_task_queues=(
            "tracecat-task-queue",
            "shared-action-queue",
        ),
        case_id="unit-scatter",
    )


def _write_runner_completion_marker(artifact_dir: Path) -> None:
    (artifact_dir / RUNNER_COMPLETE_FILENAME).write_text(
        json.dumps(
            {
                "run_id": run_id_fingerprint("scatter-test"),
                "status": "completed",
                "completed_at": "2026-07-29T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def _write_runner_artifacts(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for name in ("scenario.json", "runner_results.jsonl", "summary.txt"):
        (artifact_dir / name).write_text("{}\n", encoding="utf-8")
    _write_runner_completion_marker(artifact_dir)


def _patch_successful_auxiliary_captures(
    monkeypatch: pytest.MonkeyPatch,
    *,
    write_runner_artifacts: bool = True,
) -> None:
    def capture_compose(
        _config: CollectorConfig, artifact_dir: Path, _repo_root: Path
    ) -> str:
        if write_runner_artifacts:
            _write_runner_artifacts(artifact_dir)
        else:
            _write_runner_completion_marker(artifact_dir)
        return str(artifact_dir / "compose_config.yml")

    monkeypatch.setattr(
        collector_module,
        "capture_compose_config",
        capture_compose,
    )
    monkeypatch.setattr(
        collector_module,
        "capture_container_state",
        lambda _config, artifact_dir: str(artifact_dir / "containers.json"),
    )
    monkeypatch.setattr(
        collector_module,
        "capture_service_logs",
        lambda _config, _artifact_dir, _repo_root, *, since: {},
    )
    monkeypatch.setattr(
        collector_module,
        "capture_commit",
        lambda _repo_root: ("test-commit", False),
    )


def test_collector_requires_explicit_monitoring_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACECAT_LOADTEST_MONITOR_DSN", raising=False)

    exit_code = asyncio.run(
        collector_amain(
            [
                "--run-id",
                "scatter-test",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--cluster-num",
                "1",
                "--public-api-url",
                "http://localhost:80/api",
                "--ee-multi-tenant",
                "true",
                "--compose-file",
                "docker-compose.dev.yml",
                "--temporal-target",
                "localhost:7233",
                "--temporal-namespace",
                "default",
                "--temporal-workflow-task-queue",
                "tracecat-task-queue",
                "--temporal-activity-task-queue",
                "shared-action-queue",
            ]
        )
    )

    assert exit_code == 2


def test_collector_preflight_timeout_publishes_failed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    def time_out_preflight(
        _args: list[str],
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
        raise_on_timeout: bool = False,
    ) -> tuple[int, str, str]:
        del env
        observed_timeouts.append(timeout)
        assert raise_on_timeout
        raise collector_module.CollectorPreflightTimeoutError(
            "synthetic preflight timeout"
        )

    monkeypatch.setattr(collector_module, "_run_command", time_out_preflight)
    run_id = "scatter-preflight-timeout"
    rejected_api_url = "https://affected-tenant.example/api"
    temporal_namespace = "affected-tenant-namespace"
    temporal_queue = "affected-tenant-task-queue"
    exit_code = asyncio.run(
        collector_amain(
            [
                "--run-id",
                run_id,
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--artifact-root",
                str(tmp_path),
                "--dsn",
                "postgresql://monitor@localhost/tracecat",
                "--readiness-timeout-seconds",
                "0.03",
                "--cluster-num",
                "1",
                "--public-api-url",
                rejected_api_url,
                "--ee-multi-tenant",
                "true",
                "--compose-file",
                "docker-compose.dev.yml",
                "--temporal-target",
                "localhost:7233",
                "--temporal-namespace",
                temporal_namespace,
                "--temporal-workflow-task-queue",
                temporal_queue,
                "--temporal-activity-task-queue",
                temporal_queue,
            ]
        )
    )

    assert exit_code == 2
    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.03
    artifact_dir = tmp_path / run_id_fingerprint(run_id)
    manifest = json.loads((artifact_dir / "manifest.json").read_text())
    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "CollectorPreflightTimeoutError"
    assert manifest["sample_count"] == 0
    assert manifest["compose_project_fingerprint"] is None
    assert manifest["public_api_url"] == REDACTED_ENV_VALUE
    assert manifest["temporal_namespace"] == deployment_value_fingerprint(
        temporal_namespace
    )
    assert manifest["temporal_workflow_task_queues"] == [
        deployment_value_fingerprint(temporal_queue)
    ]
    retained_manifest = (artifact_dir / "manifest.json").read_text()
    assert rejected_api_url not in retained_manifest
    assert temporal_namespace not in retained_manifest
    assert temporal_queue not in retained_manifest
    assert (artifact_dir / collector_module.RUN_CLAIM_FILENAME).is_file()
    assert not (artifact_dir / "collector_ready.json").exists()


def test_collector_startup_signal_publishes_failed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    callbacks: dict[signal.Signals, object] = {}
    removed_signals: list[signal.Signals] = []

    async def exercise() -> int:
        loop = asyncio.get_running_loop()

        def add_signal_handler(
            sig: signal.Signals, callback: object, *_args: object
        ) -> None:
            callbacks[sig] = callback

        def remove_signal_handler(sig: signal.Signals) -> bool:
            removed_signals.append(sig)
            return True

        monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
        monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)

        def interrupt_preflight(*_args: object, **_kwargs: object) -> str:
            callback = cast(Callable[[], None], callbacks[signal.SIGTERM])
            callback()
            return "tracecat-1"

        monkeypatch.setattr(
            collector_module,
            "resolve_compose_project",
            interrupt_preflight,
        )
        monkeypatch.setattr(
            collector_module,
            "validate_running_compose_project",
            lambda *_args, **_kwargs: ComposePublicUrls(
                app="http://localhost",
                api="http://localhost/api",
            ),
        )
        monkeypatch.setattr(
            collector_module,
            "resolve_cluster_ports",
            lambda *_args, **_kwargs: ClusterPorts(
                public_api_url="http://localhost:80/api",
                postgres_host="localhost",
                postgres_port=5432,
                temporal_host="localhost",
                temporal_port=7233,
                temporal_worker_metrics_url="http://localhost:9464/metrics",
                temporal_executor_metrics_url="http://localhost:9465/metrics",
                api_db_pool_metrics_url="http://localhost:9480/db-pool-metrics",
                worker_db_pool_metrics_url="http://localhost:9481/db-pool-metrics",
                executor_db_pool_metrics_url="http://localhost:9482/db-pool-metrics",
                pgdog_metrics_url="http://localhost:9090/metrics",
            ),
        )
        monkeypatch.setattr(
            collector_module,
            "validate_public_api_url",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            collector_module,
            "validate_monitor_dsn_target",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            collector_module,
            "validate_temporal_context",
            lambda *_args, **_kwargs: None,
        )
        return await collector_amain(
            [
                "--run-id",
                "scatter-preflight-signal",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--artifact-root",
                str(tmp_path),
                "--dsn",
                "postgresql://monitor@localhost/tracecat",
                "--cluster-num",
                "1",
                "--public-api-url",
                "http://localhost:80/api",
                "--ee-multi-tenant",
                "true",
                "--compose-file",
                "docker-compose.dev.yml",
                "--temporal-target",
                "localhost:7233",
                "--temporal-namespace",
                "default",
                "--temporal-workflow-task-queue",
                "tracecat-task-queue",
                "--temporal-activity-task-queue",
                "shared-action-queue",
            ]
        )

    assert asyncio.run(exercise()) == 1
    assert set(callbacks) == {signal.SIGINT, signal.SIGTERM}
    assert set(removed_signals) == {signal.SIGINT, signal.SIGTERM}
    artifact_dir = tmp_path / run_id_fingerprint("scatter-preflight-signal")
    manifest = json.loads((artifact_dir / "manifest.json").read_text())
    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "CollectorStartupInterruptedError"
    assert not (artifact_dir / "collector_ready.json").exists()
    assert "interrupted during startup" in capsys.readouterr().err


def test_collector_fixture_table_is_not_customizable() -> None:
    assert "--table-name" not in collector_module.build_parser().format_help()


def test_runner_requires_explicit_cluster_api_url() -> None:
    with pytest.raises(SystemExit):
        build_runner_parser().parse_args([])


@pytest.mark.parametrize("load_type", tuple(LoadType))
def test_runner_exposes_each_load_type(load_type: LoadType) -> None:
    args = build_runner_parser().parse_args(
        [
            "--base-url",
            "http://localhost/api",
            "--load-type",
            load_type.value,
        ]
    )

    assert args.load_type == load_type.value
    assert "--write-path" not in build_runner_parser().format_help()


def test_each_load_type_has_one_fixture_spec() -> None:
    assert set(fixtures_module.LOAD_TYPE_FIXTURES) == set(LoadType)
    aliases = {
        spec.workflow_alias for spec in fixtures_module.LOAD_TYPE_FIXTURES.values()
    }
    assert len(aliases) == len(LoadType)
    assert all(
        spec.workflow_path.is_file()
        for spec in fixtures_module.LOAD_TYPE_FIXTURES.values()
    )


def test_runner_requires_run_id_outside_bootstrap_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(runner_module.amain(["--base-url", "http://localhost/api"]))

    assert exit_code == 2
    assert "--run-id is required" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            [
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
            ],
            "--cluster-num must be between 1 and 99",
        ),
        (
            ["--cluster-num", "1"],
            "--workspace-id is required",
        ),
    ],
)
def test_runner_requires_target_binding_arguments(
    arguments: list[str],
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--artifact-root",
                str(tmp_path),
                *arguments,
            ]
        )
    )

    assert exit_code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "scatter-test").exists()


def test_runner_existing_deployment_does_not_require_cluster_number(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--existing-deployment",
                "--workspace-id",
                "invalid",
                "--artifact-root",
                str(tmp_path),
            ]
        )
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "--workspace-id must be a valid workspace ID" in error
    assert "--cluster-num must be between" not in error


def test_runner_existing_deployment_rejects_collector_options(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--existing-deployment",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--activity-metrics-handoff",
                str(tmp_path / "handoff.json"),
                "--artifact-root",
                str(tmp_path),
            ]
        )
    )

    assert exit_code == 2
    assert "no collector is running" in capsys.readouterr().err
    assert not (tmp_path / run_id_fingerprint("scatter-test")).exists()


def test_runner_existing_deployment_rejects_cluster_number(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--existing-deployment",
                "--cluster-num",
                "1",
            ]
        )
    )

    assert exit_code == 2
    assert "cannot be used with --existing-deployment" in capsys.readouterr().err


@pytest.mark.parametrize("interval", ["0", "-0.1", "nan", "inf"])
def test_runner_rejects_nonpositive_or_nonfinite_poll_interval(
    interval: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--poll-interval-seconds",
                interval,
            ]
        )
    )

    assert exit_code == 2
    assert "--poll-interval-seconds must be finite and positive" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("--workflow-count", "0"),
        ("--workflow-count", "-1"),
        ("--branch-count", "0"),
        ("--branch-count", "-1"),
    ],
)
def test_runner_rejects_nonpositive_workload_dimensions_before_artifacts(
    argument: str,
    value: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--artifact-root",
                str(tmp_path),
                argument,
                value,
            ]
        )
    )

    assert exit_code == 2
    assert "--workflow-count and --branch-count must both be positive" in (
        capsys.readouterr().err
    )
    assert not (tmp_path / "scatter-test").exists()


def test_runner_accepts_static_scatter_branch_fanout() -> None:
    args = runner_module.build_parser().parse_args(
        [
            "--base-url",
            "http://localhost/api",
            "--load-type",
            "scatter",
            "--workflow-count",
            "4",
            "--branch-count",
            "256",
        ]
    )

    assert args.workflow_count == 4
    assert args.branch_count == 256


def test_runner_rejects_oversized_bulk_before_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "bulk-test",
                "--load-type",
                "bulk",
                "--branch-count",
                "1001",
                "--artifact-root",
                str(tmp_path),
            ]
        )
    )

    assert exit_code == 2
    assert "--branch-count must be at most 1000 for bulk loads" in (
        capsys.readouterr().err
    )
    assert not any(tmp_path.iterdir())


def test_noop_runner_omits_runtime_branch_count_input(tmp_path: Path) -> None:
    submitted_inputs: list[dict[str, object]] = []

    class RejectingClient:
        async def submit_execution(
            self,
            _workspace_id: str,
            _workflow_id: str,
            inputs: dict[str, object],
        ) -> dict[str, int | None]:
            submitted_inputs.append(inputs)
            return {"status_code": 429, "wf_exec_id": None}

    async def exercise() -> None:
        writer = JsonLinesWriter(tmp_path / "runner.jsonl")
        runner = LoadRunner(
            cast(TracecatClient, RejectingClient()),
            replace(
                _scenario_config(
                    tmp_path,
                    warmup=False,
                    workflow_count=4,
                    one_shot=True,
                ),
                load_type=LoadType.NOOP,
                branch_count=256,
            ),
            "workflow-1",
            writer,
        )
        try:
            await runner._run_one(Phase.RAMP, branch_count=256)
        finally:
            writer.close()

    asyncio.run(exercise())

    assert len(submitted_inputs) == 1
    assert "branch_count" not in submitted_inputs[0]


def test_scenario_artifacts_exclude_authentication_and_workspace_id(
    tmp_path: Path,
) -> None:
    scenario = _scenario_config(
        tmp_path,
        warmup=False,
        workflow_count=1,
        one_shot=True,
    )

    payload = _scenario_artifact_payload(scenario)

    assert "auth_email" not in payload
    assert "workspace_id" not in payload
    assert payload["run_id"] == run_id_fingerprint("scatter-test")
    assert payload["case_id"] == "unit-scatter"
    assert "scatter-test" not in json.dumps(payload)
    assert payload["workspace_fingerprint"] == workspace_fingerprint("workspace-1")
    assert payload["artifact_dir"] == (
        f"{ARTIFACT_ROOT_PLACEHOLDER}/{run_id_fingerprint('scatter-test')}"
    )
    assert str(tmp_path) not in json.dumps(payload)


def test_existing_deployment_scenario_identifies_runner_only_evidence(
    tmp_path: Path,
) -> None:
    scenario = replace(
        _scenario_config(
            tmp_path,
            warmup=False,
            workflow_count=1,
            one_shot=True,
        ),
        cluster_num=None,
        evidence_mode="runner_only",
    )

    payload = _scenario_artifact_payload(scenario)

    assert payload["cluster_num"] is None
    assert payload["evidence_mode"] == "runner_only"


def test_kubernetes_adapter_defaults_to_orbstack_public_api() -> None:
    arguments = kubernetes_module._prepare_runner_args(["--run-id", "orbstack-smoke"])

    assert arguments[:2] == [
        "--base-url",
        "https://tracecat.k8s.orb.local/api",
    ]
    assert arguments[-1] == "--existing-deployment"


def test_kubernetes_adapter_requires_url_for_another_context() -> None:
    with pytest.raises(
        kubernetes_module.KubernetesPreflightError,
        match="--base-url is required",
    ):
        kubernetes_module._prepare_runner_args(
            ["--run-id", "remote-smoke"],
            default_api_url=None,
        )


def test_kubernetes_adapter_requires_exact_current_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        kubernetes_module,
        "_run_kubectl",
        lambda _arguments: "another-context",
    )

    with pytest.raises(
        kubernetes_module.KubernetesPreflightError,
        match="expected 'orbstack'",
    ):
        kubernetes_module.verify_kubernetes_target("orbstack", "tracecat")


def test_kubernetes_adapter_checks_core_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_kubectl(arguments: tuple[str, ...]) -> str:
        calls.append(arguments)
        return "orbstack" if arguments == ("config", "current-context") else ""

    monkeypatch.setattr(kubernetes_module, "_run_kubectl", fake_kubectl)

    kubernetes_module.verify_kubernetes_target("orbstack", "tracecat")

    assert calls[0] == ("config", "current-context")
    assert [call[-2] for call in calls[1:]] == [
        "deployment/tracecat-api",
        "deployment/tracecat-worker",
        "deployment/tracecat-executor",
    ]


def test_artifact_references_redact_paths_outside_the_run_directory(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "affected-customer" / "run"

    assert (
        shareable_artifact_path(
            tmp_path / "outside.json",
            artifact_dir,
            "scatter-test",
        )
        == f"{ARTIFACT_ROOT_PLACEHOLDER}/[outside-run-directory]"
    )


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        (
            "--ramp-seconds",
            "nan",
            "--ramp-seconds and --steady-state-seconds must be finite",
        ),
        (
            "--ramp-seconds",
            "-1",
            "--ramp-seconds and --steady-state-seconds must be finite",
        ),
        (
            "--steady-state-seconds",
            "inf",
            "--ramp-seconds and --steady-state-seconds must be finite",
        ),
        (
            "--steady-state-seconds",
            "-1",
            "--ramp-seconds and --steady-state-seconds must be finite",
        ),
        (
            "--run-timeout-seconds",
            "nan",
            "--run-timeout-seconds must be finite and positive",
        ),
        (
            "--run-timeout-seconds",
            "0",
            "--run-timeout-seconds must be finite and positive",
        ),
        ("--payload-bytes", "-1", "--payload-bytes must be nonnegative"),
        ("--max-connections", "0", "--max-connections must be positive"),
    ],
)
def test_runner_rejects_invalid_numeric_dimensions(
    argument: str,
    value: str,
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--run-id",
                "scatter-test",
                "--artifact-root",
                str(tmp_path),
                argument,
                value,
            ]
        )
    )

    assert exit_code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "scatter-test").exists()


def test_workspace_bootstrap_prints_resolved_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def login(self, email: str, password: str) -> None:
            calls.append((email, password))

    async def fake_resolve_workspace(
        _client: object, workspace_id: str | None, workspace_name: str
    ) -> str:
        assert workspace_id is None
        assert workspace_name == "load-test"
        return "00000000-0000-4000-8000-000000000001"

    monkeypatch.setattr(runner_module, "TracecatClient", FakeClient)
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace",
        fake_resolve_workspace,
    )

    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost/api",
                "--bootstrap-workspace",
            ]
        )
    )

    assert exit_code == 0
    assert calls == [("dev@tracecat.com", "password1234")]
    assert capsys.readouterr().out.strip() == ("00000000-0000-4000-8000-000000000001")


def test_fixture_reset_mode_runs_without_collector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def login(self, email: str, password: str) -> None:
            calls.append((email, password))

    async def fake_reset_fixture_table(_client: object, workspace_id: str) -> str:
        assert workspace_id == "00000000-0000-4000-8000-000000000001"
        return "scatter_load_rows"

    monkeypatch.setattr(runner_module, "TracecatClient", FakeClient)
    monkeypatch.setattr(
        runner_module,
        "reset_fixture_table",
        fake_reset_fixture_table,
    )

    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost:180/api",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000001",
                "--reset-fixture-table",
            ]
        )
    )

    assert exit_code == 0
    assert calls == [("dev@tracecat.com", "password1234")]
    assert capsys.readouterr().out.strip() == (
        "reset fixture table 'scatter_load_rows' in workspace "
        f"{workspace_fingerprint('00000000-0000-4000-8000-000000000001')}"
    )


def test_fixture_reset_requires_explicit_workspace(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        runner_module.amain(
            [
                "--base-url",
                "http://localhost:180/api",
                "--reset-fixture-table",
            ]
        )
    )

    assert exit_code == 2
    assert "--workspace-id is required" in capsys.readouterr().err


@pytest.mark.parametrize("interval", ["0", "-0.1", "nan", "inf"])
def test_collector_rejects_nonpositive_or_nonfinite_intervals(interval: str) -> None:
    exit_code = asyncio.run(
        collector_amain(
            [
                "--run-id",
                "scatter-test",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--dsn",
                "postgresql://monitor@localhost/tracecat",
                "--sample-interval-seconds",
                interval,
                "--cluster-num",
                "1",
                "--public-api-url",
                "http://localhost:80/api",
                "--ee-multi-tenant",
                "true",
                "--compose-file",
                "docker-compose.dev.yml",
                "--temporal-target",
                "localhost:7233",
                "--temporal-namespace",
                "default",
                "--temporal-workflow-task-queue",
                "tracecat-task-queue",
                "--temporal-activity-task-queue",
                "shared-action-queue",
            ]
        )
    )

    assert exit_code == 2


def test_collector_default_recovery_covers_acceptance_window() -> None:
    assert collector_module.build_parser().get_default("recovery_seconds") >= 60.0


@pytest.mark.parametrize("recovery", ["-1", "0", "4.9", "nan", "inf"])
def test_collector_rejects_invalid_recovery_windows(
    recovery: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = asyncio.run(
        collector_amain(
            [
                "--run-id",
                "scatter-test",
                "--workspace-id",
                "00000000-0000-4000-8000-000000000000",
                "--dsn",
                "postgresql://monitor@localhost/tracecat",
                "--recovery-seconds",
                recovery,
                "--cluster-num",
                "1",
                "--public-api-url",
                "http://localhost:80/api",
                "--ee-multi-tenant",
                "true",
                "--compose-file",
                "docker-compose.dev.yml",
                "--temporal-target",
                "localhost:7233",
                "--temporal-namespace",
                "default",
                "--temporal-workflow-task-queue",
                "tracecat-task-queue",
                "--temporal-activity-task-queue",
                "shared-action-queue",
            ]
        )
    )

    assert exit_code == 2
    assert "--recovery-seconds must be finite and at least 5 seconds" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "loadtest_args",
    [
        ("--loadtest",),
        (
            "--compose-override",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        (
            "--compose-override=./packages/tracecat-benchmark/"
            "docker-compose.loadtest.yml",
        ),
    ],
)
def test_loadtest_up_requires_new_or_explicit_cluster(
    loadtest_args: tuple[str, ...],
) -> None:
    result = subprocess.run(
        [REPO_ROOT / "scripts/cluster", "up", "-d", *loadtest_args],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "load-test override with 'up' requires --new/-n" in result.stderr


def test_loadtest_override_exposes_resource_and_throughput_dimensions() -> None:
    loaded: object = yaml.safe_load(
        (
            REPO_ROOT / "packages/tracecat-benchmark/docker-compose.loadtest.yml"
        ).read_text(encoding="utf-8")
    )
    assert isinstance(loaded, dict)
    services = loaded.get("services")
    assert isinstance(services, dict)

    resource_services = {
        "caddy",
        "api",
        "worker",
        "executor",
        "agent-worker",
        "litellm",
        "agent-executor",
        "mcp",
        "ui",
        "postgres_db",
        "migrations",
        "temporal_postgres_db",
        "temporal",
        "temporal_ui",
        "minio",
        "redis",
    }
    for service_name in resource_services:
        service = services.get(service_name)
        assert isinstance(service, dict)
        assert {"cpus", "mem_limit", "memswap_limit"} <= service.keys()

    worker = services["worker"]
    executor = services["executor"]
    assert isinstance(worker, dict)
    assert isinstance(executor, dict)
    worker_environment = worker.get("environment")
    executor_environment = executor.get("environment")
    assert isinstance(worker_environment, dict)
    assert isinstance(executor_environment, dict)
    assert {
        "TRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS",
        "TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW",
        "TEMPORAL__THREADPOOL_MAX_WORKERS",
        "TEMPORAL__MAX_CONCURRENT_ACTIVITIES",
        "TEMPORAL__MAX_CONCURRENT_WORKFLOW_TASKS",
    } <= worker_environment.keys()
    assert worker_environment["TEMPORAL__THREADPOOL_MAX_WORKERS"] == (
        "${TRACECAT__LOADTEST_TEMPORAL_THREADPOOL_MAX_WORKERS:-100}"
    )
    assert worker_environment["TEMPORAL__MAX_CONCURRENT_ACTIVITIES"] == (
        "${TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_ACTIVITIES:-100}"
    )
    assert worker_environment["TEMPORAL__MAX_CONCURRENT_WORKFLOW_TASKS"] == (
        "${TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS:-100}"
    )
    assert worker_environment["TRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS"] == (
        "${TRACECAT__LOADTEST_DSL_SCHEDULER_MAX_PENDING_TASKS:-64}"
    )
    assert worker_environment["TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW"] == (
        "${TRACECAT__LOADTEST_CHILD_WORKFLOW_DISPATCH_WINDOW:-16}"
    )
    assert {
        "TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES",
        "TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY",
        "TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS",
    } <= executor_environment.keys()
    assert (
        executor_environment["TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY"]
        == "${TRACECAT__LOADTEST_EXECUTOR_FOR_EACH_MAX_CONCURRENCY:-4}"
    )
    assert executor_environment["TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_THREADPOOL_MAX_WORKERS:-}"
    )
    litellm = services["litellm"]
    assert isinstance(litellm, dict)
    litellm_environment = litellm.get("environment")
    assert isinstance(litellm_environment, dict)
    assert litellm_environment["TRACECAT__LITELLM_NUM_WORKERS"] == (
        "${TRACECAT__LOADTEST_LITELLM_NUM_WORKERS:-1}"
    )

    for service_name in {
        "api",
        "worker",
        "executor",
        "agent-worker",
        "mcp",
        "litellm",
        "agent-executor",
    }:
        service = services[service_name]
        assert isinstance(service, dict)
        environment = service.get("environment")
        assert isinstance(environment, dict)
        assert {
            "TRACECAT__DB_POOL_SIZE",
            "TRACECAT__DB_MAX_OVERFLOW",
            "TRACECAT__DB_AUTH_POOL_SIZE",
            "TRACECAT__DB_AUTH_MAX_OVERFLOW",
            "TRACECAT__DB_POOL_TIMEOUT",
            "TRACECAT__DB_POOL_RECYCLE",
        } <= environment.keys()

    example = matrix_module.EXPERIMENT_ENV_PATH.read_text(encoding="utf-8")
    assert "TRACECAT__LOADTEST_API_CPUS=0" in example
    assert "TRACECAT__LOADTEST_DSL_SCHEDULER_MAX_PENDING_TASKS=64" in example
    assert "TRACECAT__LOADTEST_CHILD_WORKFLOW_DISPATCH_WINDOW=16" in example
    assert "\nTRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS=" not in example
    assert "\nTRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW=" not in example
    assert "TRACECAT__LOADTEST_TEMPORAL_THREADPOOL_MAX_WORKERS=100" in example
    assert "TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_ACTIVITIES=100" in example
    assert "TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS=100" in example
    assert "\nTEMPORAL__THREADPOOL_MAX_WORKERS=" not in example
    assert "TRACECAT__LOADTEST_EXECUTOR_FOR_EACH_MAX_CONCURRENCY=4" in example
    assert "TRACECAT__LOADTEST_EXECUTOR_THREADPOOL_MAX_WORKERS=" in example
    assert "TRACECAT__LOADTEST_LITELLM_NUM_WORKERS=1" in example
    assert "\nTRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY=" not in example
    assert "\nTRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS=" not in example
    assert "TRACECAT__LOADTEST_DB_POOL_RECYCLE=600" in example
    assert "TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_POOL_SIZE=5" in example
    assert "TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_MAX_OVERFLOW=0" in example


def test_scatter_fixture_materializes_static_actions_without_for_each() -> None:
    workflow = fixtures_module.load_workflow_fixture(LoadType.SCATTER, branch_count=256)
    assert workflow.content is not None
    fixture: object = yaml.safe_load(workflow.content)
    assert isinstance(fixture, dict)
    definition = fixture.get("definition")
    assert isinstance(definition, dict)

    entrypoint = definition.get("entrypoint")
    assert isinstance(entrypoint, dict)
    expects = entrypoint.get("expects")
    assert isinstance(expects, dict)
    assert "branch_count" not in expects

    actions = definition.get("actions")
    assert isinstance(actions, list)
    assert len(actions) == 256
    assert (
        len(
            {
                action["ref"]
                for action in actions
                if isinstance(action, dict) and isinstance(action.get("ref"), str)
            }
        )
        == 256
    )
    for branch_seq, action in enumerate(actions):
        assert isinstance(action, dict)
        assert action["action"] == "core.table.insert_row"
        assert "for_each" not in action
        args = action.get("args")
        assert isinstance(args, dict)
        row_data = args.get("row_data")
        assert isinstance(row_data, dict)
        assert row_data["branch_seq"] == branch_seq
        assert str(row_data["dedupe_key"]).endswith(f":{branch_seq}")
    assert "returns" not in definition


def test_bulk_fixture_does_not_pass_row_payloads_between_actions() -> None:
    workflow = fixtures_module.load_workflow_fixture(LoadType.BULK, branch_count=256)
    fixture: object = yaml.safe_load(Path(workflow.path).read_text(encoding="utf-8"))
    assert isinstance(fixture, dict)
    definition = fixture.get("definition")
    assert isinstance(definition, dict)

    actions = definition.get("actions")
    assert isinstance(actions, list)
    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, dict)
    assert action["ref"] == "bulk_insert"
    assert action["action"] == "core.script.run_python"

    args = action.get("args")
    assert isinstance(args, dict)
    script = args.get("script")
    assert isinstance(script, str)
    assert "ctx.tables.insert_rows" in script
    assert "rows_data=rows" in script
    assert definition["returns"] == "${{ ACTIONS.bulk_insert.result }}"


def test_table_fixture_rejects_non_object_columns(tmp_path: Path) -> None:
    fixture_path = tmp_path / "table.json"
    fixture_path.write_text(
        json.dumps(
            {
                "name": "scatter_load_rows",
                "columns": [
                    {"name": "run_id", "type": "TEXT", "nullable": False},
                    "not-a-column",
                ],
                "unique_index_column": "run_id",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        fixtures_module.FixtureError,
        match=r"columns\[1\] must be a JSON object",
    ):
        fixtures_module.load_table_fixture(fixture_path)


def test_noop_fixture_materializes_static_reshape_actions_without_expressions() -> None:
    workflow = fixtures_module.load_workflow_fixture(LoadType.NOOP, branch_count=256)
    assert workflow.content is not None
    fixture: object = yaml.safe_load(workflow.content)
    assert isinstance(fixture, dict)
    definition = fixture.get("definition")
    assert isinstance(definition, dict)

    entrypoint = definition.get("entrypoint")
    assert isinstance(entrypoint, dict)
    expects = entrypoint.get("expects")
    assert isinstance(expects, dict)
    assert "branch_count" not in expects

    actions = definition.get("actions")
    assert isinstance(actions, list)
    assert len(actions) == 256
    assert (
        len(
            {
                action["ref"]
                for action in actions
                if isinstance(action, dict) and isinstance(action.get("ref"), str)
            }
        )
        == 256
    )
    for action in actions:
        assert isinstance(action, dict)
        assert action["action"] == "core.transform.reshape"
        assert "for_each" not in action
        args = action.get("args")
        assert args == {"value": None}
        assert "${{" not in yaml.safe_dump(args)
    assert "returns" not in definition


def test_new_cluster_number_skips_stopped_projects_and_retained_volumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
if [ "$1" = "compose" ] && [ "$3" = "--all" ]; then
  printf '%s\\n' '[{"Name":"tracecat-stopped-project-1"}]'
elif [ "$1" = "compose" ]; then
  printf '%s\\n' '[]'
elif [ "$1" = "volume" ]; then
  printf '%s\\n' 'tracecat-retained-volume-2'
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("PORTLESS", "0")

    result = subprocess.run(
        [REPO_ROOT / "scripts/cluster", "--loadtest", "compose-files"],
        capture_output=True,
        text=True,
        check=True,
    )

    project_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Project:")
    )
    assert project_line.strip().endswith("-3")


def test_compose_files_reuses_running_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_script = REPO_ROOT / "scripts/cluster"
    explicit = subprocess.run(
        [cluster_script, "7", "--loadtest", "compose-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    project_line = next(
        line for line in explicit.stdout.splitlines() if line.startswith("Project:")
    )
    project = project_line.partition(":")[2].strip()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '[{{\"Name\":\"{project}\"}}]'\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    automatic = subprocess.run(
        [cluster_script, "--loadtest", "compose-files"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert project_line in automatic.stdout


def test_compose_files_reuses_project_retained_only_by_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_script = REPO_ROOT / "scripts/cluster"
    explicit = subprocess.run(
        [cluster_script, "7", "--loadtest", "compose-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    project_line = next(
        line for line in explicit.stdout.splitlines() if line.startswith("Project:")
    )
    project = project_line.partition(":")[2].strip()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"""#!/bin/sh
if [ "$1" = "compose" ]; then
  printf '%s\\n' '[]'
elif [ "$1" = "volume" ]; then
  printf '%s\\n' '{project}'
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    automatic = subprocess.run(
        [cluster_script, "--loadtest", "compose-files"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert project_line in automatic.stdout


def test_existing_fixture_workflow_is_replaced_from_yaml(tmp_path: Path) -> None:
    fixture_path = tmp_path / "workflow.yml"
    fixture_path.write_text(
        "version: 1\ndefinition:\n  title: Current fixture\n",
        encoding="utf-8",
    )
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            if request.url.path.endswith("/workflow-executions/search"):
                return httpx.Response(200, json={"items": [], "next_cursor": None})
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "stale-workflow",
                            "title": "Current fixture",
                            "alias": "scatter_load_test_fixture",
                        }
                    ],
                    "next_cursor": None,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(200)
        if request.url.path.endswith("/commit"):
            return httpx.Response(200, json={"status": "success", "errors": []})
        return httpx.Response(
            201,
            json={"id": "current-workflow", "title": "Current fixture"},
        )

    async def exercise() -> str:
        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        fixture = WorkflowFixture(
            load_type=LoadType.SCATTER,
            path=str(fixture_path),
            title="Current fixture",
            alias="scatter_load_test_fixture",
        )
        try:
            return await fixtures_module._ensure_workflow(
                client, "workspace-1", fixture
            )
        finally:
            await client.aclose()

    assert asyncio.run(exercise()) == "current-workflow"
    assert [method for method, _path in requests] == [
        "GET",
        "GET",
        "DELETE",
        "POST",
        "PATCH",
        "POST",
    ]
    assert requests[1][1].endswith("/workflow-executions/search")
    assert requests[2][1].endswith("/workflows/stale-workflow")


def test_existing_fixture_workflow_is_not_replaced_while_running() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.deleted_workflow_ids: list[str] = []

        async def list_workflows(self, _workspace_id: str) -> list[dict[str, str]]:
            return [
                {
                    "id": "running-workflow",
                    "title": "Current fixture",
                    "alias": "scatter_load_test_fixture",
                }
            ]

        async def has_running_executions(
            self, _workspace_id: str, workflow_id: str
        ) -> bool:
            return workflow_id == "running-workflow"

        async def delete_workflow(self, _workspace_id: str, workflow_id: str) -> None:
            self.deleted_workflow_ids.append(workflow_id)

    async def exercise() -> FakeClient:
        client = FakeClient()
        fixture = WorkflowFixture(
            load_type=LoadType.SCATTER,
            path="unused.yml",
            title="Current fixture",
            alias="scatter_load_test_fixture",
        )
        with pytest.raises(fixtures_module.FixtureError, match="still has running"):
            await fixtures_module._ensure_workflow(
                cast(TracecatClient, client), "workspace-1", fixture
            )
        return client

    client = asyncio.run(exercise())
    assert client.deleted_workflow_ids == []


def test_same_title_without_fixture_alias_is_not_deleted(tmp_path: Path) -> None:
    fixture_path = tmp_path / "workflow.yml"
    fixture_path.write_text(
        "version: 1\ndefinition:\n  title: Current fixture\n",
        encoding="utf-8",
    )
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "unrelated-workflow",
                            "title": "Current fixture",
                            "alias": None,
                        }
                    ],
                    "next_cursor": None,
                },
            )
        if request.url.path.endswith("/commit"):
            return httpx.Response(200, json={"status": "success", "errors": []})
        if request.method == "PATCH":
            return httpx.Response(200)
        return httpx.Response(
            201,
            json={"id": "current-workflow", "title": "Current fixture"},
        )

    async def exercise() -> str:
        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        fixture = WorkflowFixture(
            load_type=LoadType.SCATTER,
            path=str(fixture_path),
            title="Current fixture",
            alias="scatter_load_test_fixture",
        )
        try:
            return await fixtures_module._ensure_workflow(
                client, "workspace-1", fixture
            )
        finally:
            await client.aclose()

    assert asyncio.run(exercise()) == "current-workflow"
    assert [method for method, _path in requests] == [
        "GET",
        "POST",
        "PATCH",
        "POST",
    ]
    assert all(method != "DELETE" for method, _path in requests)


def test_reused_fixture_table_requires_the_complete_schema() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables"):
            return httpx.Response(
                200,
                json=[{"id": "table-1", "name": "scatter_load_rows"}],
            )
        return httpx.Response(
            200,
            json={
                "id": "table-1",
                "name": "scatter_load_rows",
                "columns": [
                    {
                        "id": "column-1",
                        "name": "dedupe_key",
                        "type": "TEXT",
                        "nullable": False,
                        "is_index": True,
                    }
                ],
            },
        )

    async def exercise() -> None:
        client = TracecatClient("http://tracecat.test/api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://tracecat.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(
                fixtures_module.FixtureError, match="schema does not match"
            ):
                await fixtures_module._ensure_table(
                    client,
                    "workspace-1",
                    fixtures_module.load_table_fixture(),
                )
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_fixture_reset_recreates_only_the_checked_in_table() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.table_id: str | None = "old-table"
            self.has_unique_index = True
            self.deleted_table_ids: list[str] = []
            self.created_table_names: list[str] = []
            self.indexed_column_ids: list[str] = []

        async def list_tables(self, _workspace_id: str) -> list[dict[str, str]]:
            if self.table_id is None:
                return []
            return [{"id": self.table_id, "name": "scatter_load_rows"}]

        async def list_workflows(self, _workspace_id: str) -> list[dict[str, str]]:
            return []

        async def has_running_executions(
            self, _workspace_id: str, _workflow_id: str
        ) -> bool:
            return False

        async def get_table_columns(
            self, _workspace_id: str, _table_id: str
        ) -> list[dict[str, str | bool]]:
            return [
                {
                    "id": f"column-{index}",
                    "name": column.name,
                    "type": column.type,
                    "nullable": column.nullable,
                    "is_index": (self.has_unique_index and column.name == "dedupe_key"),
                }
                for index, column in enumerate(
                    fixtures_module.load_table_fixture().columns
                )
            ]

        async def set_column_unique_index(
            self, _workspace_id: str, _table_id: str, column_id: str
        ) -> None:
            self.indexed_column_ids.append(column_id)
            self.has_unique_index = True

        async def delete_table(self, _workspace_id: str, table_id: str) -> None:
            self.deleted_table_ids.append(table_id)
            self.table_id = None
            self.has_unique_index = False

        async def create_table(
            self,
            _workspace_id: str,
            name: str,
            _columns: list[dict[str, object]],
        ) -> None:
            self.created_table_names.append(name)
            self.table_id = "new-table"

    async def exercise() -> FakeClient:
        client = FakeClient()
        table_name = await fixtures_module.reset_fixture_table(
            cast(TracecatClient, client),
            "workspace-1",
        )
        assert table_name == "scatter_load_rows"
        return client

    client = asyncio.run(exercise())
    assert client.deleted_table_ids == ["old-table"]
    assert client.created_table_names == ["scatter_load_rows"]
    assert client.table_id == "new-table"
    assert client.indexed_column_ids == ["column-4"]


def test_fixture_reset_refuses_while_a_fixture_child_is_running() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.checked_workflow_ids: list[str] = []
            self.deleted_table_ids: list[str] = []

        async def list_tables(self, _workspace_id: str) -> list[dict[str, str]]:
            return []

        async def list_workflows(
            self, _workspace_id: str
        ) -> list[dict[str, str | None]]:
            return [
                {
                    "id": "unrelated-workflow",
                    "title": "Unrelated",
                    "alias": "unrelated",
                },
                {
                    "id": "fixture-child",
                    "title": "Scatter load subflow child",
                    "alias": fixtures_module.SUBFLOW_CHILD_ALIAS,
                },
            ]

        async def has_running_executions(
            self, _workspace_id: str, workflow_id: str
        ) -> bool:
            self.checked_workflow_ids.append(workflow_id)
            return workflow_id == "fixture-child"

        async def delete_table(self, _workspace_id: str, table_id: str) -> None:
            self.deleted_table_ids.append(table_id)

    async def exercise() -> FakeClient:
        client = FakeClient()
        with pytest.raises(
            fixtures_module.FixtureError,
            match="fixture workflows must be quiescent",
        ):
            await fixtures_module.reset_fixture_table(
                cast(TracecatClient, client),
                "workspace-1",
            )
        return client

    client = asyncio.run(exercise())
    assert client.checked_workflow_ids == ["fixture-child"]
    assert client.deleted_table_ids == []


class FailingSampler(PgSampler):
    def __init__(self) -> None:
        pass

    async def connect(self) -> None:
        raise OSError("synthetic observability loss")

    async def close(self) -> None:
        return None


class SuccessfulSampler(PgSampler):
    def __init__(
        self,
        *,
        correctness_available: bool = True,
        drift_available: bool = True,
    ) -> None:
        self._correctness_available = correctness_available
        self._drift_available = drift_available

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def probe_connection_slots(self) -> None:
        return None

    async def effective_settings(self) -> list[dict[str, str | None]]:
        return []

    async def sample(self) -> PgActivitySample:
        return PgActivitySample(
            sampled_at="2026-07-29T00:00:00+00:00",
            monotonic=0.0,
            max_connections=50,
            superuser_reserved_connections=3,
            total_connections=1,
            active=1,
            idle=0,
            idle_in_transaction=0,
            idle_in_transaction_aborted=0,
            waiting=0,
            wait_events={},
            application_names={},
            longest_transaction_seconds=None,
            longest_query_seconds=0.0,
            xact_commit_delta=0,
            xact_rollback_delta=0,
            deadlocks_delta=0,
            connection_slot_errors=0,
        )

    async def row_correctness(
        self, workspace_id: str, table_name: str, run_id: str | None
    ) -> RowCorrectness | None:
        if not self._correctness_available:
            return None
        return RowCorrectness(
            workspace_fingerprint=workspace_fingerprint(workspace_id),
            table_name=table_name,
            total_rows=1,
            distinct_dedupe_keys=1,
            duplicate_dedupe_keys=0,
            rows_for_run=1 if run_id else 0,
            distinct_dedupe_keys_for_run=1 if run_id else 0,
        )

    async def table_drift(
        self, workspace_id: str, table_name: str
    ) -> TableDrift | None:
        if not self._drift_available:
            return None
        return TableDrift(
            workspace_fingerprint=workspace_fingerprint(workspace_id),
            table_name=table_name,
            table_bytes=8192,
            indexes_bytes=16384,
            total_relation_bytes=24576,
            live_tuples=1,
            dead_tuples=0,
            inserts=1,
            updates=0,
            deletes=0,
            hot_updates=0,
            vacuum_count=0,
            autovacuum_count=1,
            analyze_count=0,
            autoanalyze_count=1,
            last_vacuum=None,
            last_autovacuum="2026-07-29 00:00:00+00:00",
            last_analyze=None,
            last_autoanalyze="2026-07-29 00:00:00+00:00",
        )


class SuccessfulTemporalSampler(TemporalSampler):
    def __init__(self) -> None:
        pass

    async def connect(self) -> None:
        return None

    async def sample(self) -> TemporalBacklogSample:
        queue_stats = TemporalTaskQueueStats(
            approximate_backlog_count=3,
            approximate_backlog_age_seconds=0.25,
            tasks_add_rate=4.0,
            tasks_dispatch_rate=2.0,
        )
        return TemporalBacklogSample(
            sampled_at="2026-07-29T00:00:00+00:00",
            monotonic=0.0,
            workflow_task_queues={
                deployment_value_fingerprint("tracecat-task-queue"): queue_stats
            },
            activity_task_queues={
                deployment_value_fingerprint("tracecat-task-queue"): queue_stats,
                deployment_value_fingerprint("shared-action-queue"): queue_stats,
            },
        )


class SuccessfulResourceSampler(DockerResourceSampler):
    def __init__(self) -> None:
        pass

    async def connect(self) -> None:
        return None

    async def sample(self) -> ResourceUsageSample:
        host = HostResourceUsage(
            logical_cpu_count=8,
            load_average_1m=1.0,
            load_average_5m=0.75,
            load_average_15m=0.5,
            memory_total_bytes=16_000_000_000,
            memory_available_bytes=8_000_000_000,
            memory_used_percent=50.0,
        )
        container = ContainerResourceUsage(
            container_id="container-1",
            service="postgres_db",
            cpu_percent=12.5,
            memory_usage_bytes=256_000_000,
            memory_limit_bytes=1_000_000_000,
            memory_percent=25.6,
            network_input_bytes=1000,
            network_output_bytes=2000,
            block_read_bytes=3000,
            block_write_bytes=4000,
            pids=6,
        )
        return ResourceUsageSample(
            sampled_at="2026-07-29T00:00:00+00:00",
            monotonic=0.0,
            host=host,
            containers=[container],
        )


class FailingResourceSampler(DockerResourceSampler):
    def __init__(self) -> None:
        pass

    async def connect(self) -> None:
        raise collector_module.ResourceUsageCaptureError(
            "synthetic resource observability loss"
        )


def _metric_collector(
    config: CollectorConfig,
    sampler: PgSampler,
) -> MetricCollector:
    return MetricCollector(
        config,
        sampler=sampler,
        temporal_sampler=SuccessfulTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )


def test_slow_temporal_sampling_does_not_throttle_postgres_samples(
    tmp_path: Path,
) -> None:
    class CountingSampler(SuccessfulSampler):
        def __init__(self) -> None:
            super().__init__()
            self.sample_calls = 0

        async def sample(self) -> PgActivitySample:
            self.sample_calls += 1
            return await super().sample()

    class SlowTemporalSampler(SuccessfulTemporalSampler):
        async def sample(self) -> TemporalBacklogSample:
            await asyncio.sleep(0.04)
            return await super().sample()

    async def exercise() -> tuple[int, int]:
        _write_runner_artifacts(tmp_path)
        pg_sampler = CountingSampler()
        collector = MetricCollector(
            replace(
                _collector_config(tmp_path),
                sample_interval_seconds=0.01,
                recovery_seconds=0.12,
            ),
            sampler=pg_sampler,
            temporal_sampler=SlowTemporalSampler(),
            resource_sampler=SuccessfulResourceSampler(),
        )
        await collector._run_sampling_loops(
            tmp_path / "pg_activity.jsonl",
            tmp_path / "temporal_backlog.jsonl",
            tmp_path / "resource_usage.jsonl",
        )
        return pg_sampler.sample_calls, collector._temporal_sample_count

    pg_samples, temporal_samples = asyncio.run(exercise())

    assert pg_samples >= 5
    assert temporal_samples <= 3


def test_temporal_cadence_miss_invalidates_observability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowTemporalSampler(SuccessfulTemporalSampler):
        async def sample(self) -> TemporalBacklogSample:
            await asyncio.sleep(0.02)
            return await super().sample()

    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            recovery_seconds=0.01,
        ),
        sampler=SuccessfulSampler(),
        temporal_sampler=SlowTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["case_id"] == "unit-scatter"
    assert manifest["observability_failure"] == "SamplingCadenceError"
    temporal_records = [
        json.loads(line)
        for line in (artifact_dir / "temporal_backlog.jsonl").read_text().splitlines()
    ]
    assert any(
        record.get("sampling_gap") == "cadence_delayed"
        and record.get("signal") == "Temporal"
        for record in temporal_records
    )


def test_recovered_sampler_error_records_nonfatal_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientTemporalSampler(SuccessfulTemporalSampler):
        def __init__(self) -> None:
            self.sample_calls = 0

        async def sample(self) -> TemporalBacklogSample:
            self.sample_calls += 1
            if self.sample_calls == 2:
                raise OSError("synthetic transient scrape failure")
            return await super().sample()

    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            recovery_seconds=0.05,
        ),
        sampler=SuccessfulSampler(),
        temporal_sampler=TransientTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "completed"
    assert manifest["observability_failure"] is None
    temporal_records = [
        json.loads(line)
        for line in (artifact_dir / "temporal_backlog.jsonl").read_text().splitlines()
    ]
    assert any(
        record.get("sampling_gap") == "sampler_error"
        and record.get("signal") == "Temporal"
        and record.get("error_type") == "OSError"
        for record in temporal_records
    )


def test_recovered_postgres_sampler_error_still_invalidates_observability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientPostgresSampler(SuccessfulSampler):
        def __init__(self) -> None:
            super().__init__()
            self.sample_calls = 0

        async def sample(self) -> PgActivitySample:
            self.sample_calls += 1
            if self.sample_calls == 2:
                raise OSError("synthetic transient PostgreSQL scrape failure")
            return await super().sample()

    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            recovery_seconds=0.05,
        ),
        sampler=TransientPostgresSampler(),
        temporal_sampler=SuccessfulTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "OSError"
    postgres_records = [
        json.loads(line)
        for line in (artifact_dir / "pg_activity.jsonl").read_text().splitlines()
    ]
    assert any(
        record.get("sampling_gap") == "sampler_error"
        and record.get("signal") == "PostgreSQL"
        and record.get("error_type") == "OSError"
        for record in postgres_records
    )


def test_unrecovered_sampler_error_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TerminalTemporalSampler(SuccessfulTemporalSampler):
        def __init__(self) -> None:
            self.sample_calls = 0

        async def sample(self) -> TemporalBacklogSample:
            self.sample_calls += 1
            if self.sample_calls > 1:
                raise OSError("synthetic terminal scrape failure")
            return await super().sample()

    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            recovery_seconds=0.05,
        ),
        sampler=SuccessfulSampler(),
        temporal_sampler=TerminalTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "OSError"


def test_resource_cadence_miss_invalidates_observability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowResourceSampler(SuccessfulResourceSampler):
        async def sample(self) -> ResourceUsageSample:
            await asyncio.sleep(0.02)
            return await super().sample()

    monkeypatch.setattr(
        collector_module,
        "RESOURCE_SAMPLE_INTERVAL_SECONDS",
        0.01,
    )
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            recovery_seconds=0.01,
        ),
        sampler=SuccessfulSampler(),
        temporal_sampler=SuccessfulTemporalSampler(),
        resource_sampler=SlowResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "SamplingCadenceError"
    resource_records = [
        json.loads(line)
        for line in (artifact_dir / "resource_usage.jsonl").read_text().splitlines()
    ]
    assert any(
        record.get("sampling_gap") == "cadence_delayed"
        and record.get("signal") == "Resource"
        for record in resource_records
    )


def test_resource_cadence_allows_normal_docker_stats_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _metric_collector(
        _collector_config(tmp_path),
        SuccessfulSampler(),
    )
    assert collector_module.RESOURCE_SAMPLE_INTERVAL_SECONDS == 3.0
    sink = tmp_path / "resource_usage.jsonl"

    with sink.open("x", encoding="utf-8") as handle:
        monkeypatch.setattr(
            "tracecat_benchmark.collector.time.monotonic",
            lambda: 2.5,
        )
        collector._record_cadence_failure(
            handle,
            signal_name="Resource",
            tick=0.0,
            interval_seconds=collector_module.RESOURCE_SAMPLE_INTERVAL_SECONDS,
        )

    assert sink.read_text(encoding="utf-8") == ""


def test_collector_readiness_deadline_publishes_failed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverReadyTemporalSampler(SuccessfulTemporalSampler):
        async def sample(self) -> TemporalBacklogSample:
            raise OSError("synthetic startup outage")

    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            readiness_timeout_seconds=0.03,
        ),
        sampler=SuccessfulSampler(),
        temporal_sampler=NeverReadyTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "SamplingReadinessError"
    assert manifest["readiness_timeout_seconds"] == 0.03
    assert collector._stop.is_set()
    assert not (artifact_dir / "collector_ready.json").exists()
    temporal_records = [
        json.loads(line)
        for line in (artifact_dir / "temporal_backlog.jsonl").read_text().splitlines()
    ]
    assert any(
        record.get("observability_failure") == "SamplingReadinessError"
        for record in temporal_records
    )


def test_collector_readiness_deadline_includes_sampler_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverConnectTemporalSampler(SuccessfulTemporalSampler):
        async def connect(self) -> None:
            await asyncio.Event().wait()

    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        replace(
            _collector_config(artifact_dir),
            readiness_timeout_seconds=10.0,
        ),
        sampler=SuccessfulSampler(),
        temporal_sampler=NeverConnectTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    async def exercise() -> CollectorManifest:
        return await collector.run(
            tmp_path,
            startup_deadline=time.monotonic() + 0.03,
        )

    manifest = asyncio.run(asyncio.wait_for(exercise(), timeout=0.5))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "SamplingReadinessError"
    assert collector._stop.is_set()
    assert not (artifact_dir / "collector_ready.json").exists()


def test_sampling_stops_only_after_runner_completion_and_recovery(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        collector = _metric_collector(
            replace(
                _collector_config(tmp_path),
                sample_interval_seconds=0.01,
                recovery_seconds=0.02,
            ),
            SuccessfulSampler(),
        )
        sample_task = asyncio.create_task(
            collector._run_sampling_loops(
                tmp_path / "pg_activity.jsonl",
                tmp_path / "temporal_backlog.jsonl",
                tmp_path / "resource_usage.jsonl",
            )
        )

        await asyncio.sleep(0.04)
        assert not sample_task.done()
        assert not collector._stop.is_set()

        (tmp_path / RUNNER_COMPLETE_FILENAME).write_text(
            json.dumps(
                {
                    "run_id": run_id_fingerprint("scatter-test"),
                    "status": "completed",
                    "completed_at": "2026-07-29T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        await asyncio.wait_for(sample_task, timeout=1.0)
        assert collector._stop.is_set()

    asyncio.run(exercise())


def test_interrupted_recovery_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = _metric_collector(
        replace(
            _collector_config(artifact_dir),
            sample_interval_seconds=0.01,
            recovery_seconds=0.5,
        ),
        SuccessfulSampler(),
    )

    async def exercise() -> CollectorManifest:
        run_task = asyncio.create_task(collector.run(tmp_path))
        async with asyncio.timeout(1.0):
            while collector._runner_status is None:
                await asyncio.sleep(0.01)
        collector.request_stop()
        return await run_task

    manifest = asyncio.run(exercise())

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "RecoveryWindowInterruptedError"


def test_aborted_runner_marks_manifest_aborted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"

    def capture_aborted_runner(
        _config: CollectorConfig,
        captured_dir: Path,
        _repo_root: Path,
    ) -> str:
        _write_runner_artifacts(captured_dir)
        (captured_dir / RUNNER_COMPLETE_FILENAME).write_text(
            json.dumps(
                {
                    "run_id": run_id_fingerprint("scatter-test"),
                    "status": "aborted",
                    "completed_at": "2026-07-29T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return str(captured_dir / "compose_config.yml")

    monkeypatch.setattr(
        collector_module,
        "capture_compose_config",
        capture_aborted_runner,
    )

    manifest = asyncio.run(
        _metric_collector(
            _collector_config(artifact_dir),
            SuccessfulSampler(),
        ).run(tmp_path)
    )

    assert manifest["status"] == "aborted"
    assert manifest["observability_failure"] is None
    persisted = json.loads((artifact_dir / "manifest.json").read_text())
    assert persisted["status"] == "aborted"
    assert persisted["observability_failure"] is None


def test_manifest_fingerprints_temporal_deployment_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    temporal_target = "affected-tenant.example:7233"
    temporal_namespace = "affected-tenant-namespace"
    workflow_queue = "affected-tenant-workflow-queue"
    activity_queue = "affected-tenant-activity-queue"
    config = replace(
        _collector_config(artifact_dir),
        temporal_target=temporal_target,
        temporal_namespace=temporal_namespace,
        temporal_workflow_task_queues=(workflow_queue,),
        temporal_activity_task_queues=(workflow_queue, activity_queue),
    )

    manifest = asyncio.run(_metric_collector(config, SuccessfulSampler()).run(tmp_path))

    assert manifest["temporal_target"] == deployment_value_fingerprint(temporal_target)
    assert manifest["temporal_namespace"] == deployment_value_fingerprint(
        temporal_namespace
    )
    assert manifest["temporal_workflow_task_queues"] == (
        deployment_value_fingerprint(workflow_queue),
    )
    assert manifest["temporal_activity_task_queues"] == (
        deployment_value_fingerprint(workflow_queue),
        deployment_value_fingerprint(activity_queue),
    )
    retained_manifest = (artifact_dir / "manifest.json").read_text()
    for raw_value in (
        temporal_target,
        temporal_namespace,
        workflow_queue,
        activity_queue,
    ):
        assert raw_value not in retained_manifest


def test_observability_loss_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)

    artifact_dir = tmp_path / "scatter-test"
    config = _collector_config(artifact_dir)

    manifest = asyncio.run(_metric_collector(config, FailingSampler()).run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "OSError"
    assert json.loads((artifact_dir / "manifest.json").read_text())["status"] == (
        "observability_failed"
    )
    activity_record = json.loads(
        (artifact_dir / "pg_activity.jsonl").read_text().strip()
    )
    assert activity_record["observability_failure"] == "OSError"


def test_sampling_task_group_failure_still_writes_failed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"

    class TaskGroupFailingCollector(MetricCollector):
        async def _run_sampling_loops(
            self,
            pg_sink: Path,
            temporal_sink: Path,
            resource_sink: Path,
        ) -> None:
            for sink in (pg_sink, temporal_sink, resource_sink):
                sink.write_text("{}\n", encoding="utf-8")
            self._sample_count = 1
            self._temporal_sample_count = 1
            self._resource_sample_count = 1
            self._pg_ready.set()
            self._temporal_ready.set()
            self._resource_ready.set()
            await asyncio.sleep(0.01)
            raise ExceptionGroup(
                "unhandled sampling task failure",
                [OSError("synthetic artifact flush failure")],
            )

    collector = TaskGroupFailingCollector(
        _collector_config(artifact_dir),
        sampler=SuccessfulSampler(),
        temporal_sampler=SuccessfulTemporalSampler(),
        resource_sampler=SuccessfulResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "OSError"
    persisted = json.loads((artifact_dir / "manifest.json").read_text())
    assert persisted["status"] == "observability_failed"
    assert persisted["observability_failure"] == "OSError"


def test_resource_observability_loss_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"
    collector = MetricCollector(
        _collector_config(artifact_dir),
        sampler=SuccessfulSampler(),
        temporal_sampler=SuccessfulTemporalSampler(),
        resource_sampler=FailingResourceSampler(),
    )

    manifest = asyncio.run(collector.run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "ResourceUsageCaptureError"
    resource_record = json.loads(
        (artifact_dir / "resource_usage.jsonl").read_text().strip()
    )
    assert resource_record["observability_failure"] == "ResourceUsageCaptureError"


def test_collector_rejects_nonempty_artifact_directory(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "scatter-test"
    artifact_dir.mkdir()
    existing_path = artifact_dir / "pg_activity.jsonl"
    existing_content = '{"sampled_at":"existing"}\n'
    existing_path.write_text(existing_content, encoding="utf-8")

    with pytest.raises(
        collector_module.ArtifactDirectoryReuseError,
        match="artifact directory is not empty",
    ):
        asyncio.run(
            _metric_collector(
                _collector_config(artifact_dir),
                SuccessfulSampler(),
            ).run(tmp_path)
        )

    assert existing_path.read_text() == existing_content
    assert not (artifact_dir / collector_module.RUN_CLAIM_FILENAME).exists()


def test_compose_capture_runs_through_cluster_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_args: list[str] = []
    recorded_env: dict[str, str] = {}

    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del timeout
        recorded_args.extend(args)
        recorded_env.update(env or {})
        return 0, "services: {}\n", ""

    monkeypatch.setattr(collector_module, "_run_command", fake_run_command)
    config = CollectorConfig(
        run_id="scatter-test",
        workspace_id="00000000-0000-4000-8000-000000000000",
        artifact_dir=str(tmp_path),
        dsn="postgresql://monitor@localhost/tracecat",
        sample_interval_seconds=0.5,
        readiness_timeout_seconds=1.0,
        cluster_num=2,
        public_api_url="http://localhost:180/api",
        compose_public_app_url="https://c2.tracecat.localhost",
        compose_public_api_url="https://c2.tracecat.localhost/api",
        ee_multi_tenant=True,
        compose_project="tracecat-test-2",
        compose_files=(
            "docker-compose.dev.yml",
            "docker-compose.sandbox.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        log_services=(),
        recovery_seconds=30.0,
        temporal_target="localhost:7333",
        temporal_namespace="default",
        temporal_workflow_task_queues=("tracecat-task-queue",),
        temporal_activity_task_queues=(
            "tracecat-task-queue",
            "shared-action-queue",
        ),
    )

    capture_compose_config(config, tmp_path, REPO_ROOT)

    assert recorded_args[0] == str(REPO_ROOT / "scripts/cluster")
    assert recorded_args[-1] == "config"
    assert recorded_args[1:7] == [
        "2",
        "--profile",
        "dev",
        "--sandbox",
        "--ee-multi-tenant",
        "true",
    ]
    assert "--compose-override" in recorded_args
    assert recorded_env["CLUSTER_PUBLIC_API_URL_OVERRIDE"] == (
        "https://c2.tracecat.localhost/api"
    )
    assert recorded_env["CLUSTER_PUBLIC_APP_URL_OVERRIDE"] == (
        "https://c2.tracecat.localhost"
    )
    assert (tmp_path / "compose_config.yml").read_text() == "services: {}\n"


@pytest.mark.parametrize(
    ("mismatch", "expected_error"),
    [
        (None, None),
        ("files", "ordered Compose files"),
        ("hash", "does not match the deployed container"),
        ("replica-hash", "replicas for executor have different"),
        ("stopped", "stopped required services: worker"),
    ],
)
def test_running_compose_project_matches_files_and_service_hashes(
    mismatch: str | None,
    expected_error: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_files = (
        "docker-compose.dev.yml",
        "docker-compose.sandbox.yml",
        "packages/tracecat-benchmark/docker-compose.loadtest.yml",
    )
    compose_project = "tracecat-test-2"
    required_services = (
        "api",
        "caddy",
        "executor",
        "minio",
        "postgres_db",
        "redis",
        "temporal",
        "temporal_postgres_db",
        "worker",
    )
    assert collector_module.REQUIRED_LOAD_TEST_SERVICES == frozenset(required_services)
    deployed_services = (*required_services, "migrations")
    expected_hashes = {
        service: f"{index:064x}"
        for index, service in enumerate(deployed_services, start=1)
    }
    deployed_instances = list(deployed_services)
    if mismatch == "replica-hash":
        deployed_instances.append("executor")
    deployed_app_url = "https://c2.tracecat.localhost"
    deployed_api_url = f"{deployed_app_url}/api"
    expected_files = [str((REPO_ROOT / path).resolve()) for path in compose_files]
    deployed_files = list(expected_files)
    if mismatch == "files":
        deployed_files[1], deployed_files[2] = (
            deployed_files[2],
            deployed_files[1],
        )

    labels_by_service: list[dict[str, str]] = []
    for instance_index, service in enumerate(deployed_instances):
        config_hash = expected_hashes[service]
        if mismatch == "hash" and service == "worker":
            config_hash = "f" * 64
        if mismatch == "replica-hash" and instance_index == len(deployed_services):
            config_hash = "e" * 64
        labels_by_service.append(
            {
                "com.docker.compose.project": compose_project,
                "com.docker.compose.project.config_files": ",".join(deployed_files),
                "com.docker.compose.project.working_dir": str(REPO_ROOT),
                "com.docker.compose.service": service,
                "com.docker.compose.config-hash": config_hash,
            }
        )

    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del timeout
        if args[:2] == ["docker", "ps"]:
            return (
                0,
                "\n".join(
                    f"{service}-container-{index}"
                    for index, service in enumerate(deployed_instances)
                )
                + "\n",
                "",
            )
        if args[:2] == ["docker", "inspect"]:
            environment = json.dumps(
                [
                    f"TRACECAT__PUBLIC_APP_URL={deployed_app_url}",
                    f"TRACECAT__PUBLIC_API_URL={deployed_api_url}",
                ]
            )
            records = [
                f"{json.dumps(labels)}\t"
                f"{str(service != 'migrations' and not (mismatch == 'stopped' and service == 'worker')).lower()}\t"
                f"{environment}"
                for service, labels in zip(
                    deployed_instances, labels_by_service, strict=True
                )
            ]
            return 0, "\n".join(records) + "\n", ""
        if args[-3:] == ["config", "--hash", "*"]:
            assert env is not None
            assert env["CLUSTER_PUBLIC_APP_URL_OVERRIDE"] == deployed_app_url
            assert env["CLUSTER_PUBLIC_API_URL_OVERRIDE"] == deployed_api_url
            return (
                0,
                "\n".join(
                    f"{service} {expected_hashes[service]}"
                    for service in deployed_services
                )
                + "\n",
                "",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(collector_module, "_run_command", fake_run_command)
    if expected_error is None:
        deployed_urls = validate_running_compose_project(
            REPO_ROOT,
            2,
            compose_files,
            True,
            compose_project,
        )
        assert deployed_urls.app == deployed_app_url
        assert deployed_urls.api == deployed_api_url
    else:
        with pytest.raises(
            collector_module.CollectorConfigurationError,
            match=expected_error,
        ):
            validate_running_compose_project(
                REPO_ROOT,
                2,
                compose_files,
                True,
                compose_project,
            )


TEMPORAL_CONTEXT_COMPOSE_CONFIG = """
services:
  worker:
    environment:
      TEMPORAL__CLUSTER_NAMESPACE: default
      TEMPORAL__CLUSTER_QUEUE: tracecat-task-queue
  executor:
    environment:
      TEMPORAL__CLUSTER_NAMESPACE: default
      TRACECAT__EXECUTOR_QUEUE: shared-action-queue
"""


def _mock_cluster_context(
    monkeypatch: pytest.MonkeyPatch, repo_root: Path
) -> ClusterPorts:
    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        del timeout, env
        if args[-1] == "ports":
            return (
                0,
                "  API:             http://localhost:180/api (internal: 8000)\n"
                "  PostgreSQL:      localhost:5532\n"
                "  Temporal:        localhost:7333\n"
                "  Worker metrics:  http://localhost:9564/metrics\n"
                "  Executor metrics: http://localhost:9565/metrics\n"
                "  API DB pool metrics: http://localhost:9580/db-pool-metrics\n"
                "  Worker DB pool metrics: http://localhost:9581/db-pool-metrics\n"
                "  Executor DB pool metrics: http://localhost:9582/db-pool-metrics\n"
                "  PgDog metrics:   http://localhost:9592/metrics\n",
                "",
            )
        if args[-1] == "config":
            return 0, TEMPORAL_CONTEXT_COMPOSE_CONFIG, ""
        raise AssertionError(f"unexpected cluster command: {args[-1]}")

    monkeypatch.setattr(collector_module, "_run_command", fake_run_command)
    return resolve_cluster_ports(
        repo_root,
        2,
        (
            "docker-compose.dev.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        True,
    )


@pytest.mark.parametrize(
    "public_api_url",
    [
        "http://localhost:180/api",
        "http://127.0.0.1:180/api/",
        "http://[::1]:180/api",
    ],
)
def test_public_api_url_must_target_selected_cluster(
    public_api_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)
    validate_public_api_url(public_api_url, cluster_ports)


@pytest.mark.parametrize(
    "public_api_url",
    [
        "http://localhost:181/api",
        "https://localhost:180/api",
        "http://api.example.test:180/api",
        "http://localhost:180/not-api",
        "http://localhost:180/api?cluster=2",
    ],
)
def test_public_api_url_rejects_different_cluster_endpoint(
    public_api_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)
    with pytest.raises(
        collector_module.CollectorConfigurationError,
        match="public API URL",
    ):
        validate_public_api_url(public_api_url, cluster_ports)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://monitor@localhost:5532/postgres",
        "postgresql://monitor@127.0.0.1:5532/postgres",
        "postgres://monitor@[::1]:5532/postgres",
    ],
)
def test_monitor_dsn_must_target_selected_cluster(
    dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)
    validate_monitor_dsn_target(dsn, cluster_ports)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://monitor@localhost:5432/postgres",
        "postgresql://monitor@db.example.test:5532/postgres",
    ],
)
def test_monitor_dsn_rejects_different_cluster_endpoint(
    dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)
    with pytest.raises(
        collector_module.CollectorConfigurationError,
        match="does not match the selected cluster",
    ):
        validate_monitor_dsn_target(dsn, cluster_ports)


def test_temporal_sdk_metrics_accept_scaled_executor_port_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)

    validate_temporal_sdk_metrics_urls(
        "http://127.0.0.1:9564/metrics",
        (
            "http://localhost:9565/metrics",
            "http://127.0.0.1:9566/metrics",
            "http://[::1]:9567/metrics",
        ),
        cluster_ports,
    )


@pytest.mark.parametrize(
    "executor_urls",
    [
        (
            "http://localhost:9565/metrics",
            "http://localhost:9565/metrics",
        ),
        ("http://localhost:9575/metrics",),
    ],
)
def test_temporal_sdk_metrics_reject_invalid_scaled_executor_endpoints(
    executor_urls: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)

    with pytest.raises(collector_module.CollectorConfigurationError):
        validate_temporal_sdk_metrics_urls(
            "http://localhost:9564/metrics",
            executor_urls,
            cluster_ports,
        )


def test_temporal_context_must_match_selected_cluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)

    validate_temporal_context(
        "127.0.0.1:7333",
        "default",
        ("tracecat-task-queue",),
        ("tracecat-task-queue", "shared-action-queue"),
        cluster_ports,
        tmp_path,
        2,
        (
            "docker-compose.dev.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        True,
        "http://localhost:180/api",
    )


@pytest.mark.parametrize(
    (
        "temporal_target",
        "temporal_namespace",
        "workflow_task_queues",
        "activity_task_queues",
    ),
    [
        (
            "localhost:7233",
            "default",
            ("tracecat-task-queue",),
            ("shared-action-queue",),
        ),
        (
            "localhost:7333",
            "another-namespace",
            ("tracecat-task-queue",),
            ("shared-action-queue",),
        ),
        (
            "localhost:7333",
            "default",
            ("another-workflow-queue",),
            ("shared-action-queue",),
        ),
        (
            "localhost:7333",
            "default",
            ("tracecat-task-queue",),
            ("another-activity-queue",),
        ),
        (
            "localhost:7333",
            "default",
            ("tracecat-task-queue",),
            ("shared-action-queue",),
        ),
    ],
)
def test_temporal_context_rejects_mismatched_cluster_values(
    temporal_target: str,
    temporal_namespace: str,
    workflow_task_queues: tuple[str, ...],
    activity_task_queues: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_ports = _mock_cluster_context(monkeypatch, tmp_path)

    with pytest.raises(
        collector_module.CollectorConfigurationError,
        match="not match",
    ):
        validate_temporal_context(
            temporal_target,
            temporal_namespace,
            workflow_task_queues,
            activity_task_queues,
            cluster_ports,
            tmp_path,
            2,
            (
                "docker-compose.dev.yml",
                "packages/tracecat-benchmark/docker-compose.loadtest.yml",
            ),
            True,
            "http://localhost:180/api",
        )


def test_compose_capture_failure_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_compose_capture(
        _config: CollectorConfig, artifact_dir: Path, _repo_root: Path
    ) -> str:
        _write_runner_artifacts(artifact_dir)
        (artifact_dir / "compose_config.yml").write_text(
            "# capture failed\n", encoding="utf-8"
        )
        raise collector_module.ComposeConfigCaptureError(
            "synthetic Compose capture failure"
        )

    _patch_successful_auxiliary_captures(monkeypatch)
    monkeypatch.setattr(
        collector_module,
        "capture_compose_config",
        fail_compose_capture,
    )

    artifact_dir = tmp_path / "scatter-test"
    config = _collector_config(artifact_dir)

    manifest = asyncio.run(_metric_collector(config, SuccessfulSampler()).run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "ComposeConfigCaptureError"
    assert "compose_config" not in manifest["artifacts"]


def test_missing_row_correctness_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)

    artifact_dir = tmp_path / "scatter-test"
    config = _collector_config(artifact_dir)

    manifest = asyncio.run(
        _metric_collector(
            config,
            SuccessfulSampler(correctness_available=False),
        ).run(tmp_path)
    )

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "RowCorrectnessCaptureError"
    assert "row_correctness" not in manifest["artifacts"]


def test_table_drift_is_a_required_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    raw_run_id = "scatter-test"
    raw_compose_project = "tracecat-affected-customer-1"
    artifact_dir = tmp_path / run_id_fingerprint(raw_run_id)

    manifest = asyncio.run(
        _metric_collector(
            replace(
                _collector_config(artifact_dir),
                compose_project=raw_compose_project,
            ),
            SuccessfulSampler(),
        ).run(tmp_path)
    )

    assert manifest["status"] == "completed"
    assert manifest["compose_project_fingerprint"] == compose_project_fingerprint(
        raw_compose_project
    )
    shareable_artifact_dir = (
        f"{ARTIFACT_ROOT_PLACEHOLDER}/{run_id_fingerprint(raw_run_id)}"
    )
    assert manifest["artifact_dir"] == shareable_artifact_dir
    assert manifest["artifacts"]["table_drift"] == (
        f"{shareable_artifact_dir}/table_drift.json"
    )
    drift = json.loads((artifact_dir / "table_drift.json").read_text())
    assert drift["total_relation_bytes"] == 24576
    assert drift["autovacuum_count"] == 1
    temporal_sample = json.loads(
        (artifact_dir / "temporal_backlog.jsonl").read_text().strip()
    )
    assert (
        temporal_sample["workflow_task_queues"][
            deployment_value_fingerprint("tracecat-task-queue")
        ]["approximate_backlog_count"]
        == 3
    )
    readiness = json.loads((artifact_dir / "collector_ready.json").read_text())
    assert readiness["status"] == "ready"
    assert readiness["run_id"] == run_id_fingerprint(raw_run_id)
    assert readiness["cluster_num"] == 1
    assert readiness["public_api_url"] == "http://localhost:80/api"
    assert readiness["workspace_fingerprint"] == workspace_fingerprint(
        "00000000-0000-4000-8000-000000000000"
    )
    assert readiness["sample_count"] == 1
    assert readiness["temporal_sample_count"] == 1
    assert readiness["resource_sample_count"] == 1
    resource_sample = json.loads(
        (artifact_dir / "resource_usage.jsonl").read_text().strip()
    )
    assert resource_sample["host"]["memory_used_percent"] == 50.0
    assert resource_sample["containers"][0]["block_write_bytes"] == 4000
    raw_workspace_id = "00000000-0000-4000-8000-000000000000"
    for artifact in artifact_dir.iterdir():
        if artifact.is_file():
            artifact_text = artifact.read_text(encoding="utf-8")
            assert raw_workspace_id not in artifact_text
            assert raw_run_id not in artifact_text
            assert raw_compose_project not in artifact_text
            assert str(tmp_path) not in artifact_text


def test_compose_capture_starts_after_initial_metric_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_dir = tmp_path / "scatter-test"
    _patch_successful_auxiliary_captures(monkeypatch)

    def assert_samples_ready(
        _config: CollectorConfig, captured_dir: Path, _repo_root: Path
    ) -> str:
        assert (captured_dir / "collector_ready.json").is_file()
        assert (captured_dir / "pg_activity.jsonl").stat().st_size > 0
        assert (captured_dir / "temporal_backlog.jsonl").stat().st_size > 0
        assert (captured_dir / "resource_usage.jsonl").stat().st_size > 0
        _write_runner_artifacts(captured_dir)
        return str(captured_dir / "compose_config.yml")

    monkeypatch.setattr(
        collector_module,
        "capture_compose_config",
        assert_samples_ready,
    )

    manifest = asyncio.run(
        _metric_collector(
            _collector_config(artifact_dir),
            SuccessfulSampler(),
        ).run(tmp_path)
    )

    assert manifest["status"] == "completed"


def test_missing_table_drift_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    artifact_dir = tmp_path / "scatter-test"

    manifest = asyncio.run(
        _metric_collector(
            _collector_config(artifact_dir),
            SuccessfulSampler(drift_available=False),
        ).run(tmp_path)
    )

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "TableDriftCaptureError"
    assert "table_drift" not in manifest["artifacts"]


def test_empty_container_project_is_a_capture_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        collector_module,
        "_run_command",
        lambda _args, **_kwargs: (0, "", ""),
    )
    config = _collector_config(tmp_path)

    with pytest.raises(collector_module.ContainerStateCaptureError):
        capture_container_state(config, tmp_path)


def test_container_state_retains_service_and_image_id_without_project_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_project = "tracecat-affected-customer-1"

    def fake_run_command(
        args: list[str],
        **_kwargs: object,
    ) -> tuple[int, str, str]:
        if args[1] == "ps":
            return 0, "container-1\n", ""
        return (
            0,
            json.dumps(
                [
                    {
                        "Name": f"/{raw_project}-api-1",
                        "Image": "sha256:0123456789abcdef",
                        "RestartCount": 0,
                        "State": {
                            "Status": "running",
                            "OOMKilled": False,
                            "ExitCode": 0,
                        },
                        "HostConfig": {
                            "NanoCpus": 1_000_000_000,
                            "Memory": 1_000_000_000,
                            "MemorySwap": 1_000_000_000,
                            "PidsLimit": 100,
                        },
                        "Config": {
                            "Image": f"{raw_project}-api",
                            "Labels": {"com.docker.compose.service": "api"},
                        },
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(collector_module, "_run_command", fake_run_command)
    config = replace(_collector_config(tmp_path), compose_project=raw_project)

    capture_container_state(config, tmp_path)

    retained = (tmp_path / "containers.json").read_text(encoding="utf-8")
    assert raw_project not in retained
    assert json.loads(retained) == [
        {
            "service": "api",
            "image_id": "sha256:0123456789abcdef",
            "status": "running",
            "restart_count": 0,
            "oom_killed": False,
            "exit_code": 0,
            "nano_cpus": 1_000_000_000,
            "memory_limit_bytes": 1_000_000_000,
            "memory_swap_limit_bytes": 1_000_000_000,
            "pids_limit": 100,
        }
    ]


def test_container_capture_failure_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)

    def fail_container_capture(_config: CollectorConfig, _artifact_dir: Path) -> str:
        raise collector_module.ContainerStateCaptureError(
            "synthetic container capture failure"
        )

    monkeypatch.setattr(
        collector_module,
        "capture_container_state",
        fail_container_capture,
    )
    artifact_dir = tmp_path / "scatter-test"
    config = _collector_config(artifact_dir)

    manifest = asyncio.run(_metric_collector(config, SuccessfulSampler()).run(tmp_path))

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "ContainerStateCaptureError"
    assert "containers" not in manifest["artifacts"]


def test_missing_runner_artifacts_mark_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(
        monkeypatch,
        write_runner_artifacts=False,
    )
    artifact_dir = tmp_path / "scatter-test"

    manifest = asyncio.run(
        _metric_collector(
            _collector_config(artifact_dir),
            SuccessfulSampler(),
        ).run(tmp_path)
    )

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "RunnerArtifactsCaptureError"


def test_service_log_capture_failure_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)
    monkeypatch.setattr(
        collector_module,
        "capture_service_logs",
        capture_service_logs,
    )
    monkeypatch.setattr(
        collector_module,
        "_run_command",
        lambda _args, **_kwargs: (1, "", "synthetic log failure"),
    )
    artifact_dir = tmp_path / "scatter-test"

    manifest = asyncio.run(
        _metric_collector(
            _collector_config(artifact_dir, log_services=("api",)),
            SuccessfulSampler(),
        ).run(tmp_path)
    )

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "ServiceLogCaptureError"
    assert "log:api" not in manifest["artifacts"]
    assert not (artifact_dir / "logs" / "api.json").exists()


def test_extra_log_services_preserve_required_diagnostics() -> None:
    assert _resolve_log_services(["api", "temporal"]) == (
        *DEFAULT_LOG_SERVICES,
        "temporal",
    )


def test_invalid_extra_log_service_is_rejected() -> None:
    with pytest.raises(
        collector_module.CollectorConfigurationError,
        match="invalid Compose service name",
    ):
        _resolve_log_services(["api; synthetic-command"])


def test_service_logs_retain_only_aggregate_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = "00000000-0000-4000-8000-000000000000"
    short_id = "ws_000000001vGeH72LxVtxKg"
    raw_log = "\n".join(
        (
            f"workspace={workspace_id} schema=tables_{short_id}",
            "user=synthetic@example.test url=https://internal.example.test/private",
            "token=synthetic-secret-value",
            "QueuePool limit reached; connection timed out",
            "remaining connection slots are reserved",
            "canceling statement due to statement timeout",
            "canceling statement due to lock timeout",
            'request completed HTTP/1.1" 503',
        )
    )

    commands: list[list[str]] = []

    def capture_logs(
        args: list[str],
        **_kwargs: object,
    ) -> tuple[int, str, str]:
        commands.append(args)
        return 0, raw_log, ""

    monkeypatch.setattr(
        collector_module,
        "_run_command",
        capture_logs,
    )
    config = _collector_config(tmp_path, log_services=("api",))
    since = "2026-07-30T06:00:00+00:00"

    written = capture_service_logs(config, tmp_path, tmp_path, since=since)

    retained = Path(written["api"]).read_text(encoding="utf-8")
    assert json.loads(retained) == {
        "service": "api",
        "since": since,
        "lines_scanned": 8,
        "signal_counts": {
            "postgres_connection_limit": 1,
            "database_pool_timeout": 1,
            "statement_timeout": 1,
            "lock_timeout": 1,
            "deadlock": 0,
            "serialization_failure": 0,
            "connection_refused": 0,
            "connection_reset": 0,
            "timeout": 1,
            "http_5xx": 1,
        },
    }
    assert commands[0][-5:] == ["logs", "--no-color", "--since", since, "api"]
    for raw_value in (
        workspace_id,
        short_id,
        "synthetic@example.test",
        "https://internal.example.test/private",
        "synthetic-secret-value",
        "QueuePool",
        "canceling statement due to statement timeout",
        "canceling statement due to lock timeout",
    ):
        assert raw_value not in retained


def test_commit_capture_failure_marks_manifest_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_auxiliary_captures(monkeypatch)

    def fail_commit_capture(_repo_root: Path) -> tuple[str, bool]:
        raise collector_module.CommitCaptureError("synthetic commit capture failure")

    monkeypatch.setattr(
        collector_module,
        "capture_commit",
        fail_commit_capture,
    )
    artifact_dir = tmp_path / "scatter-test"

    manifest = asyncio.run(
        _metric_collector(
            _collector_config(artifact_dir),
            SuccessfulSampler(),
        ).run(tmp_path)
    )

    assert manifest["status"] == "observability_failed"
    assert manifest["observability_failure"] == "CommitCaptureError"
    assert manifest["tracecat_commit"] == "unknown"
