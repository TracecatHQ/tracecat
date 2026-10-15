"""Rerun a past workflow execution with results reused from successful actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import FixedActionResult
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowID,
    WorkflowIDShort,
)

ROOT_SCOPE = "<root>"
CONTROL_FLOW_ACTIONS = frozenset(
    {
        PlatformAction.TRANSFORM_SCATTER,
        PlatformAction.TRANSFORM_GATHER,
        PlatformAction.LOOP_START,
        PlatformAction.LOOP_END,
    }
)


class WorkflowRerunActionStatus(StrEnum):
    FIXED = "fixed"
    """The source run's result is reused instead of running the action."""
    EXCLUDED = "excluded"
    """The source run's result could be reused but the caller chose to run it."""
    RUNS = "runs"
    """The action runs because the source run has no reusable result for it."""
    NOT_IN_TARGET = "not_in_target"
    """The source run has a result but the target has no action with this ref."""
    ACTION_CHANGED = "action_changed"
    """The target action with this ref uses a different action type."""


class WorkflowRerunRequest(BaseModel):
    use_draft: bool = Field(
        default=True,
        description="Rerun against the current draft. Set false to use a published version.",
    )
    version: int | None = Field(
        default=None,
        description=(
            "Published version to rerun against when use_draft is false. "
            "Null uses the current published version."
        ),
    )
    fixed_action_refs: list[str] | None = Field(
        default=None,
        description=(
            "Action refs whose results to reuse. Null reuses every matching "
            "successful action; an empty list reruns everything."
        ),
    )


class WorkflowRerunActionMatch(BaseModel):
    ref: str
    status: WorkflowRerunActionStatus
    source_action: str | None = None
    target_action: str | None = None
    reason: str | None = None


class WorkflowRerunPlan(BaseModel):
    source_execution_id: WorkflowExecutionID
    workflow_id: WorkflowIDShort
    use_draft: bool
    version: int | None = None
    """Published version the rerun targets. Null for drafts."""
    actions: list[WorkflowRerunActionMatch]
    fixed_action_refs: list[str]
    unmatched_refs: list[str]
    """Refs with a reusable source result that the target cannot use."""
    inputs_error: str | None = None
    """Set when the source run's trigger inputs fail the target's input schema."""


class WorkflowRerunResponse(BaseModel):
    message: str
    wf_id: WorkflowIDShort
    wf_exec_id: WorkflowExecutionID
    plan: WorkflowRerunPlan


@dataclass(slots=True)
class RerunSourceAction:
    ref: str
    action: str
    fixed: FixedActionResult | None = None
    """Reusable result. None when the action did not succeed."""
    reason: str | None = None
    """Why the action has no reusable result."""


@dataclass(slots=True)
class RerunSource:
    execution_id: WorkflowExecutionID
    workflow_id: WorkflowID
    trigger_inputs: Any | None
    actions: dict[str, RerunSourceAction] = field(default_factory=dict)


@dataclass(slots=True)
class RerunMatch:
    actions: list[WorkflowRerunActionMatch]
    fixed: dict[str, FixedActionResult]
    unmatched_refs: list[str]


def _target_ineligible_reason(dsl: DSLInput) -> dict[str, str]:
    scopes, _, _ = dsl._assign_action_scopes(dsl._to_adjacency())
    reasons: dict[str, str] = {}
    for stmt in dsl.actions:
        if stmt.action in CONTROL_FLOW_ACTIONS:
            reasons[stmt.ref] = "Control flow actions always run."
        elif scopes.get(stmt.ref, ROOT_SCOPE) != ROOT_SCOPE:
            reasons[stmt.ref] = "Actions inside a scatter or loop always run."
        elif stmt.for_each is not None:
            reasons[stmt.ref] = "Actions with for_each always run."
    return reasons


def match_rerun_actions(
    source: RerunSource,
    dsl: DSLInput,
    *,
    fixed_action_refs: list[str] | None = None,
) -> RerunMatch:
    """Match the source run's action results against a target definition."""
    ineligible = _target_ineligible_reason(dsl)
    requested = None if fixed_action_refs is None else set(fixed_action_refs)
    actions: list[WorkflowRerunActionMatch] = []
    fixed: dict[str, FixedActionResult] = {}
    target_refs: set[str] = set()

    for stmt in dsl.actions:
        target_refs.add(stmt.ref)
        src = source.actions.get(stmt.ref)
        if src is None:
            actions.append(
                WorkflowRerunActionMatch(
                    ref=stmt.ref,
                    status=WorkflowRerunActionStatus.RUNS,
                    target_action=stmt.action,
                    reason="Not run in the source run.",
                )
            )
            continue
        if src.fixed is None:
            actions.append(
                WorkflowRerunActionMatch(
                    ref=stmt.ref,
                    status=WorkflowRerunActionStatus.RUNS,
                    source_action=src.action,
                    target_action=stmt.action,
                    reason=src.reason,
                )
            )
            continue
        if src.action != stmt.action:
            actions.append(
                WorkflowRerunActionMatch(
                    ref=stmt.ref,
                    status=WorkflowRerunActionStatus.ACTION_CHANGED,
                    source_action=src.action,
                    target_action=stmt.action,
                    reason=f"Action changed from {src.action} to {stmt.action}.",
                )
            )
            continue
        if reason := ineligible.get(stmt.ref):
            actions.append(
                WorkflowRerunActionMatch(
                    ref=stmt.ref,
                    status=WorkflowRerunActionStatus.RUNS,
                    source_action=src.action,
                    target_action=stmt.action,
                    reason=reason,
                )
            )
            continue
        if requested is not None and stmt.ref not in requested:
            actions.append(
                WorkflowRerunActionMatch(
                    ref=stmt.ref,
                    status=WorkflowRerunActionStatus.EXCLUDED,
                    source_action=src.action,
                    target_action=stmt.action,
                )
            )
            continue
        fixed[stmt.ref] = src.fixed
        actions.append(
            WorkflowRerunActionMatch(
                ref=stmt.ref,
                status=WorkflowRerunActionStatus.FIXED,
                source_action=src.action,
                target_action=stmt.action,
            )
        )

    for ref, src in source.actions.items():
        if ref in target_refs or src.fixed is None:
            continue
        actions.append(
            WorkflowRerunActionMatch(
                ref=ref,
                status=WorkflowRerunActionStatus.NOT_IN_TARGET,
                source_action=src.action,
                reason="No action with this ref in the target.",
            )
        )

    unmatched_refs = [
        a.ref
        for a in actions
        if a.status
        in (
            WorkflowRerunActionStatus.NOT_IN_TARGET,
            WorkflowRerunActionStatus.ACTION_CHANGED,
        )
    ]
    return RerunMatch(actions=actions, fixed=fixed, unmatched_refs=unmatched_refs)


def invalid_requested_refs(
    match: RerunMatch, fixed_action_refs: list[str] | None
) -> list[str]:
    """Requested refs that cannot be fixed."""
    if fixed_action_refs is None:
        return []
    return [ref for ref in fixed_action_refs if ref not in match.fixed]
