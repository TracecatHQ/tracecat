"""Trace spans for the phases inside one action execution.

`tracecat.action.execute` used to be a single opaque span. These tests pin the
child spans that break it down: host-measured phases (registry resolution,
sandbox preparation, the jailed process, result decoding, cleanup) and the
phases the sandboxed runner reports through `result.json` timing marks. The
marks come from jailed code, so the host only turns them into spans when they
stay inside the process window it measured itself.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import orjson
import pytest
import tracecat_registry
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pytest_mock import MockerFixture

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor import minimal_runner
from tracecat.executor.backends import registry_helpers
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.observability.otel import (
    PlatformTracing,
    initialize_platform_tracing,
    shutdown_platform_tracing,
)
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.nsjail_protocol import NsjailCompletedProcess
from tracecat.sandbox.result_envelope import decode_result_envelope
from tracecat.sandbox.types import RunnerPhaseMarks

_RUNNER_PHASES = (
    "tracecat.action.runner.startup",
    "tracecat.action.runner.load_input",
    "tracecat.action.runner.import",
    "tracecat.action.runner.call",
    "tracecat.action.runner.finish",
)


@pytest.fixture
def tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[PlatformTracing, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    runtime = initialize_platform_tracing("tracecat-executor", exporter=exporter)
    assert runtime is not None
    try:
        yield runtime, exporter
    finally:
        shutdown_platform_tracing()


def _spans_by_name(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {span.name: span for span in exporter.get_finished_spans()}


def _span_id(span: ReadableSpan) -> int:
    assert span.context is not None
    return span.context.span_id


def _parent_id(span: ReadableSpan) -> int:
    assert span.parent is not None
    return span.parent.span_id


async def _run_jail(
    tmp_path: Path,
    mocker: MockerFixture,
    runtime: PlatformTracing,
    phase_ns: Any,
) -> Any:
    """Run NsjailExecutor.execute_action against a fake jail.

    ``phase_ns`` is either the literal marks to write, or a callable that
    builds them while the fake jail "runs", so they land inside the host's
    measured process window.
    """
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    async def fake_invoke_nsjail(**_kwargs: Any) -> NsjailCompletedProcess:
        marks = phase_ns() if callable(phase_ns) else phase_ns
        envelope = {"success": True, "result": {"ok": True}, "phase_ns": marks}
        (job_dir / "result.json").write_bytes(orjson.dumps(envelope))
        return NsjailCompletedProcess(
            returncode=0,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            nsjail_log=b"",
            nsjail_signaled=False,
        )

    mocker.patch("tracecat.sandbox.executor.invoke_nsjail", fake_invoke_nsjail)
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    mocker.patch.object(executor, "_build_action_config", return_value="")
    mocker.patch.object(executor, "_build_action_env_map", return_value={})
    action_config = ActionSandboxConfig(
        registry_paths=[],
        tracecat_app_dir=tmp_path,
        site_packages_dir=None,
    )
    with runtime.tracer("test").start_as_current_span("tracecat.action.execute"):
        return await executor.execute_action(job_dir, action_config)


def _backwards_marks() -> dict[str, int]:
    """Two in-window marks where the later phase claims the earlier time."""
    earlier = time.time_ns()
    while (later := time.time_ns()) == earlier:
        pass
    return {"main": later, "input_loaded": earlier}


def _marks_in_window() -> dict[str, int]:
    return {
        "main": time.time_ns(),
        "input_loaded": time.time_ns(),
        "imported": time.time_ns(),
        "called": time.time_ns(),
    }


@pytest.mark.anyio
async def test_jailed_process_span_breaks_into_runner_phases(
    tmp_path: Path,
    mocker: MockerFixture,
    tracing: tuple[PlatformTracing, InMemorySpanExporter],
) -> None:
    """Runner marks become contiguous child spans of the jailed process.

    Startup runs from the host's spawn to the runner's first mark, and the
    finish phase from the last mark to the host seeing the process exit, so
    the five phases tile the whole process span with no gaps.
    """
    runtime, exporter = tracing
    reported: dict[str, int] = {}

    def marks() -> dict[str, int]:
        reported.update(_marks_in_window())
        return reported

    result = await _run_jail(tmp_path, mocker, runtime, marks)

    assert result.success is True
    assert result.output == {"ok": True}
    spans = _spans_by_name(exporter)
    execute = spans["tracecat.action.execute"]
    process = spans["tracecat.action.sandbox.process"]
    read_result = spans["tracecat.action.sandbox.read_result"]
    assert _parent_id(process) == _span_id(execute)
    assert _parent_id(read_result) == _span_id(execute)

    phases = [spans[name] for name in _RUNNER_PHASES]
    for phase in phases:
        assert _parent_id(phase) == _span_id(process)
    assert process.start_time is not None
    assert phases[0].start_time is not None
    assert phases[0].start_time >= process.start_time
    assert [phase.end_time for phase in phases[:-1]] == [
        reported["main"],
        reported["input_loaded"],
        reported["imported"],
        reported["called"],
    ]
    for earlier, later in zip(phases, phases[1:], strict=False):
        assert earlier.end_time == later.start_time
    assert process.end_time is not None
    assert phases[-1].end_time is not None
    assert phases[-1].end_time <= process.end_time


@pytest.mark.anyio
@pytest.mark.parametrize(
    "phase_ns",
    [
        pytest.param({"main": 1, "called": 2}, id="before-spawn"),
        pytest.param({"main": 2**62}, id="after-exit"),
        pytest.param(_backwards_marks, id="runs-backwards"),
    ],
)
async def test_forged_runner_marks_are_dropped_without_failing_the_action(
    tmp_path: Path,
    mocker: MockerFixture,
    tracing: tuple[PlatformTracing, InMemorySpanExporter],
    phase_ns: Any,
) -> None:
    """Jailed code cannot place spans outside what the host measured."""
    runtime, exporter = tracing

    result = await _run_jail(tmp_path, mocker, runtime, phase_ns)

    assert result.success is True
    spans = _spans_by_name(exporter)
    assert "tracecat.action.sandbox.process" in spans
    assert not set(_RUNNER_PHASES) & set(spans)


@pytest.mark.anyio
async def test_runner_phases_stop_at_the_last_mark_reached(
    tmp_path: Path,
    mocker: MockerFixture,
    tracing: tuple[PlatformTracing, InMemorySpanExporter],
) -> None:
    """A module that fails to import never reports ``imported``.

    The phases on either side of the missing mark are dropped; the rest still
    show where the time went.
    """
    runtime, exporter = tracing

    def marks() -> dict[str, int]:
        reported = _marks_in_window()
        del reported["imported"]
        return reported

    await _run_jail(tmp_path, mocker, runtime, marks)

    phases = set(_RUNNER_PHASES) & set(_spans_by_name(exporter))
    assert phases == {
        "tracecat.action.runner.startup",
        "tracecat.action.runner.load_input",
        "tracecat.action.runner.finish",
    }


@pytest.mark.parametrize(
    ("phase_ns", "expected"),
    [
        pytest.param("not-a-map", None, id="not-a-map"),
        pytest.param({"main": True, "called": "1"}, None, id="non-int-values"),
        pytest.param(
            {"main": 5, "called": 1.5, "secret": 7},
            RunnerPhaseMarks(main=5),
            id="keeps-only-known-int-marks",
        ),
    ],
)
def test_malformed_phase_marks_never_reject_the_result(
    tmp_path: Path,
    phase_ns: object,
    expected: RunnerPhaseMarks | None,
) -> None:
    (tmp_path / "result.json").write_bytes(
        orjson.dumps({"success": True, "result": 1, "phase_ns": phase_ns})
    )

    outcome = decode_result_envelope(
        tmp_path,
        output_key="result",
        stdout="",
        stderr="",
        stderr_limit=2000,
        invalid_result_error="invalid",
        log_label="action",
        exit_code=0,
        execution_time_ms=1.0,
        max_bytes=1 << 20,
        stream_source="process",
        include_error_code=True,
    )

    assert outcome is not None
    assert outcome.valid_envelope is True
    assert outcome.result.success is True
    assert outcome.result.phase_marks == expected


def test_runner_marks_the_import_and_call_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(minimal_runner, "_phase_ns", {})

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "tracecat_registry.core.transform",
                    "name": "deduplicate",
                },
                "evaluated_args": {"items": [{"a": 1}], "keys": [], "persist": False},
            },
        }
    )

    assert result["success"] is True
    marks = minimal_runner._phase_ns
    assert set(marks) == {"imported", "called"}
    assert marks["imported"] <= marks["called"]


def _registry_input(version: str) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(action="core.transform.reshape", args={}, ref="a"),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": version},
            actions={"core.transform.reshape": "tracecat_registry"},
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("version", "expected_source", "expected_lookups"),
    [
        pytest.param(tracecat_registry.__version__, "bundled", 0, id="bundled"),
        pytest.param("0.0.0-other", "artifact", 1, id="artifact"),
    ],
)
async def test_registry_resolution_span_records_the_builtin_source(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tracing: tuple[PlatformTracing, InMemorySpanExporter],
    version: str,
    expected_source: str,
    expected_lookups: int,
) -> None:
    """The span says whether the builtin came from the image or a stored artifact.

    A stored artifact is downloaded and mounted on first use, which is the
    expensive cold path; the image copy costs nothing.
    """
    _runtime, exporter = tracing
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", False)
    lookup = mocker.patch.object(
        registry_helpers,
        "get_registry_artifacts_for_lock",
        new=AsyncMock(return_value=[]),
    )
    role = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )

    await registry_helpers.get_registry_artifact_uris(_registry_input(version), role)

    span = _spans_by_name(exporter)["tracecat.action.resolve_registry_artifacts"]
    assert span.attributes is not None
    assert span.attributes["tracecat.registry.builtin_source"] == expected_source
    assert span.attributes["tracecat.registry.artifact_origin_count"] == (
        expected_lookups
    )
    assert lookup.await_count == expected_lookups
