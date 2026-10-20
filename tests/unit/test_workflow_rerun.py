from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    ExecutionContext,
    FixedActionResult,
    RunContext,
    TaskResult,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage.object import ExternalObject, ObjectRef
from tracecat.workflow.executions.rerun import (
    MAX_FIXED_RESULTS_BYTES,
    RerunSource,
    RerunSourceAction,
    WorkflowRerunActionStatus,
    expire_stored_results,
    invalid_requested_refs,
    match_rerun_actions,
)

SOURCE_EXEC_ID = (
    "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000"
)


def _fixed(value: object, event_id: int = 1) -> FixedActionResult:
    return FixedActionResult(
        result=TaskResult.from_result(value),
        source_execution_id=SOURCE_EXEC_ID,
        source_event_id=event_id,
    )


def _source(**actions: RerunSourceAction) -> RerunSource:
    return RerunSource(
        execution_id=SOURCE_EXEC_ID,
        workflow_id=WorkflowUUID.new_uuid4(),
        trigger_inputs=None,
        actions=dict(actions),
    )


def _dsl(*actions: ActionStatement) -> DSLInput:
    return DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref=actions[0].ref),
        actions=list(actions),
    )


def _statuses(source: RerunSource, dsl: DSLInput, **kwargs) -> dict[str, str]:
    match = match_rerun_actions(source, dsl, **kwargs)
    return {a.ref: a.status for a in match.actions}


def test_match_fixes_successful_actions_with_same_ref_and_action() -> None:
    source = _source(
        a=RerunSourceAction(ref="a", action="core.noop", fixed=_fixed(1)),
        b=RerunSourceAction(ref="b", action="core.noop", reason="failed"),
        gone=RerunSourceAction(ref="gone", action="core.noop", fixed=_fixed(2)),
        swapped=RerunSourceAction(ref="swapped", action="core.noop", fixed=_fixed(3)),
    )
    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
        ActionStatement(
            ref="swapped", action="core.transform.reshape", depends_on=["a"]
        ),
        ActionStatement(ref="new", action="core.noop", depends_on=["a"]),
    )
    match = match_rerun_actions(source, dsl)
    statuses = {a.ref: a.status for a in match.actions}
    assert statuses == {
        "a": WorkflowRerunActionStatus.FIXED,
        "b": WorkflowRerunActionStatus.RUNS,
        "swapped": WorkflowRerunActionStatus.ACTION_CHANGED,
        "new": WorkflowRerunActionStatus.RUNS,
        "gone": WorkflowRerunActionStatus.NOT_IN_TARGET,
    }
    assert list(match.fixed) == ["a"]
    assert match.unmatched_refs == ["swapped", "gone"]


def test_match_never_fixes_control_flow_or_scoped_actions() -> None:
    source = _source(
        a=RerunSourceAction(ref="a", action="core.noop", fixed=_fixed(1)),
        scatter=RerunSourceAction(
            ref="scatter", action="core.transform.scatter", fixed=_fixed([1])
        ),
        inner=RerunSourceAction(ref="inner", action="core.noop", fixed=_fixed(2)),
    )
    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(
            ref="scatter",
            action="core.transform.scatter",
            depends_on=["a"],
            args={"collection": "${{ [1, 2] }}"},
        ),
        ActionStatement(ref="inner", action="core.noop", depends_on=["scatter"]),
        ActionStatement(
            ref="gather",
            action="core.transform.gather",
            depends_on=["inner"],
            args={"items": "${{ ACTIONS.inner.result }}"},
        ),
    )
    statuses = _statuses(source, dsl)
    assert statuses["a"] == WorkflowRerunActionStatus.FIXED
    assert statuses["scatter"] == WorkflowRerunActionStatus.RUNS
    assert statuses["inner"] == WorkflowRerunActionStatus.RUNS


def test_match_respects_requested_refs() -> None:
    source = _source(
        a=RerunSourceAction(ref="a", action="core.noop", fixed=_fixed(1)),
        b=RerunSourceAction(ref="b", action="core.noop", fixed=_fixed(2)),
    )
    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
    )
    match = match_rerun_actions(source, dsl, fixed_action_refs=["a", "missing"])
    statuses = {a.ref: a.status for a in match.actions}
    assert statuses["a"] == WorkflowRerunActionStatus.FIXED
    assert statuses["b"] == WorkflowRerunActionStatus.EXCLUDED
    assert invalid_requested_refs(match, ["a", "missing"]) == ["missing"]
    assert match_rerun_actions(source, dsl, fixed_action_refs=[]).fixed == {}


def test_match_reruns_from_ref_and_downstream() -> None:
    source = _source(
        **{
            r: RerunSourceAction(ref=r, action="core.noop", fixed=_fixed(i))
            for i, r in enumerate("abcd")
        }
    )
    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
        ActionStatement(ref="c", action="core.noop", depends_on=["b.error"]),
        ActionStatement(ref="d", action="core.noop", depends_on=["a"]),
    )
    match = match_rerun_actions(source, dsl, rerun_from_ref="b")
    statuses = {a.ref: a.status for a in match.actions}
    assert set(match.fixed) == {"a", "d"}
    assert statuses["b"] == WorkflowRerunActionStatus.EXCLUDED
    assert statuses["c"] == WorkflowRerunActionStatus.EXCLUDED
    with pytest.raises(ValueError):
        match_rerun_actions(source, dsl, rerun_from_ref="missing")


def test_match_runs_fixed_results_over_the_payload_budget_fresh() -> None:
    big = "x" * (MAX_FIXED_RESULTS_BYTES // 2 + 1)
    source = _source(
        a=RerunSourceAction(
            ref="a", action="core.transform.reshape", fixed=_fixed(big)
        ),
        b=RerunSourceAction(
            ref="b", action="core.transform.reshape", fixed=_fixed(big)
        ),
        c=RerunSourceAction(ref="c", action="core.transform.reshape", fixed=_fixed(1)),
    )
    dsl = _dsl(
        ActionStatement(ref="a", action="core.transform.reshape"),
        ActionStatement(ref="b", action="core.transform.reshape"),
        ActionStatement(ref="c", action="core.transform.reshape"),
    )
    match = match_rerun_actions(source, dsl)
    assert set(match.fixed) == {"a", "c"}
    b = next(a for a in match.actions if a.ref == "b")
    assert b.status == WorkflowRerunActionStatus.RUNS
    assert b.reason == "Too much result data to reuse in one run."
    assert invalid_requested_refs(
        match_rerun_actions(source, dsl, fixed_action_refs=["a", "b"]), ["a", "b"]
    ) == ["b"]


def test_expire_stored_results_drops_blob_backed_results_past_retention() -> None:
    external = FixedActionResult(
        result=TaskResult(
            result=ExternalObject(
                ref=ObjectRef(
                    backend="s3", bucket="b", key="k", size_bytes=1, sha256="0" * 64
                )
            ),
            result_typename="dict",
        ),
        source_execution_id=SOURCE_EXEC_ID,
        source_event_id=1,
    )
    now = datetime.now(UTC)

    def _src() -> RerunSource:
        return _source(
            inline=RerunSourceAction(ref="inline", action="x", fixed=_fixed(1)),
            external=RerunSourceAction(ref="external", action="x", fixed=external),
        )

    recent = _src()
    expire_stored_results(recent, now - timedelta(days=29), now=now, retention_days=30)
    assert recent.actions["external"].fixed is external

    old = _src()
    expire_stored_results(old, now - timedelta(days=31), now=now, retention_days=30)
    assert old.actions["external"].fixed is None
    assert old.actions["external"].reason == "Its stored result expired after 30 days."
    assert old.actions["inline"].fixed is not None

    disabled = _src()
    expire_stored_results(
        disabled, now - timedelta(days=365), now=now, retention_days=0
    )
    assert disabled.actions["external"].fixed is external


def _scheduler(
    dsl: DSLInput, executed: list[str], fixed: dict[str, TaskResult]
) -> DSLScheduler:
    async def executor(stmt: ActionStatement) -> None:
        executed.append(stmt.ref)
        context["ACTIONS"][stmt.ref] = TaskResult.from_result(f"ran-{stmt.ref}")

    wf_id = WorkflowUUID.new_uuid4()
    context = ExecutionContext(ACTIONS={}, TRIGGER=None)
    return DSLScheduler(
        executor=executor,
        dsl=dsl,
        max_pending_tasks=16,
        context=context,
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
        ),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        fixed_action_results=fixed,
    )


@pytest.mark.anyio
async def test_scheduler_uses_fixed_results_instead_of_executing() -> None:
    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
        ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
    )
    executed: list[str] = []
    fixed = {
        "a": TaskResult.from_result("fixed-a"),
        "b": TaskResult.from_result("fixed-b"),
    }
    scheduler = _scheduler(dsl, executed, fixed)
    await scheduler.start()
    assert executed == ["c"]
    assert scheduler.applied_fixed_refs == ["a", "b"]
    actions = scheduler.get_context(ROOT_STREAM)["ACTIONS"]
    assert actions["a"] is fixed["a"]
    assert actions["b"] is fixed["b"]


@pytest.mark.anyio
async def test_scheduler_does_not_apply_fixed_result_when_run_if_skips() -> None:
    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(
            ref="b", action="core.noop", depends_on=["a"], run_if="${{ False }}"
        ),
        ActionStatement(ref="c", action="core.noop", depends_on=["a"]),
    )
    executed: list[str] = []
    fixed = {"b": TaskResult.from_result("fixed-b")}
    scheduler = _scheduler(dsl, executed, fixed)

    async def resolve_expression(expr: str, context: object) -> bool:
        return False

    # Expression evaluation needs the workflow event loop.
    scheduler.resolve_expression = resolve_expression  # type: ignore[method-assign]
    await scheduler.start()
    assert sorted(executed) == ["a", "c"]
    assert scheduler.applied_fixed_refs == []
