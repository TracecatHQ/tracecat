"""Workflow identifiers."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Query
from pydantic import UUID4, StringConstraints

from tracecat.identifiers.common import TracecatUUID
from tracecat.identifiers.resource import ResourcePrefix, generate_resource_id
from tracecat.identifiers.schedules import SCHEDULE_EXEC_ID_PATTERN

# Patterns
WF_ID_PREFIX = "wf_"
WF_ID_SHORT_PATTERN = rf"{WF_ID_PREFIX}[0-9a-zA-Z]+"
EXEC_ID_PREFIX = "exec_"
EXEC_ID_SHORT_PATTERN = rf"{EXEC_ID_PREFIX}[0-9a-zA-Z]+"
WS_ID_PREFIX = "ws_"
WS_ID_SHORT_PATTERN = rf"{WS_ID_PREFIX}[0-9a-zA-Z]+"

LEGACY_WF_ID_PATTERN = r"wf-[0-9a-f]{32}"
LEGACY_EXEC_ID_PATTERN = r"exec-[\w-]+"
WF_EXEC_SUFFIX_PATTERN = (
    rf"({EXEC_ID_SHORT_PATTERN}|{LEGACY_EXEC_ID_PATTERN}|{SCHEDULE_EXEC_ID_PATTERN})"
)
WF_EXEC_ID_PATTERN = rf"(?P<workflow_id>{LEGACY_WF_ID_PATTERN}|{WF_ID_SHORT_PATTERN})[:/](?P<execution_id>{WF_EXEC_SUFFIX_PATTERN})"

WorkflowIDShort = Annotated[str, StringConstraints(pattern=WF_ID_SHORT_PATTERN)]
"""A short base62 encoded string representation of a workflow UUID.

Examples
--------
- long ->  `8d3885a9-6470-4ee0-9d4d-d507cc97393d`
- short -> `wf_4itKqkgCZrLhgYiq5L211X`
"""

ExecutionIDShort = Annotated[str, StringConstraints(pattern=EXEC_ID_SHORT_PATTERN)]
"""A short base62 encoded string representation of a workflow execution UUID.

Examples
--------
- long ->  `8d3885a9-6470-4ee0-9d4d-d507cc97393d`
- short -> `exec_4itKqkgCZrLhgYiq5L211X`
"""

WorkspaceIDShort = Annotated[str, StringConstraints(pattern=WS_ID_SHORT_PATTERN)]
"""A short base62 encoded string representation of a workspace UUID.

Examples
--------
- long ->  `8d3885a9-6470-4ee0-9d4d-d507cc97393d`
- short -> `ws_4itKqkgCZrLhgYiq5L211X`
"""


class WorkflowUUID(TracecatUUID[WorkflowIDShort]):
    """UUID for workflow resources."""

    prefix = WF_ID_PREFIX
    legacy_prefix = "wf-"


class ExecutionUUID(TracecatUUID[ExecutionIDShort]):
    """UUID for workflow execution resources."""

    prefix = EXEC_ID_PREFIX
    legacy_prefix = "exec-"


class WorkspaceUUID(TracecatUUID[WorkspaceIDShort]):
    """UUID for workspace resources."""

    prefix = WS_ID_PREFIX


# Annotations
WorkflowID = WorkflowUUID
"""A unique ID for a workflow.

This is the logical equivalent of a workflow definition ID in Temporal.

Exapmles
--------
- `1234-5678-90ab-cdef-1234567890ab`


References
----------
See Temporal docs: https://docs.temporal.io/workflows#workflow-id
"""
LegacyWorkflowID = Annotated[str, StringConstraints(pattern=LEGACY_WF_ID_PATTERN)]


AnyWorkflowID = WorkflowID | WorkflowIDShort | LegacyWorkflowID
"""A workflow ID that can be either a UUID or a short string."""

AnyExecutionID = ExecutionUUID | ExecutionIDShort
"""Either manual or scheduled execution ID."""

AnyWorkspaceID = WorkspaceUUID | WorkspaceIDShort | UUID4
"""A workspace ID that can be either a UUID or a short string."""

WorkflowExecutionID = Annotated[str, StringConstraints(pattern=WF_EXEC_ID_PATTERN)]
"""The full unique ID for a workflow execution.

Not to be confused with the run ID, which is a UUID for each run/retry of the execution.

Examples
--------
- Normal execution: `wf-1234567890abcdef1234567890abcdef:exec-140a425a577932a0c95edcfb8465a1a`
- Scheduled execution: `wf-1234567890abcdef1234567890abcdef:sch-140a425a577932a0c95edcfb8465a1a-2021-09-01T00:00:00Z`

References
----------
See Temporal docs: https://docs.temporal.io/workflows#workflow-id
"""

WorkflowRunID = UUID4
"""A UUID4 identifier for each try of a workflow execution.

References
----------
See the Temporal equivalent: https://docs.temporal.io/workflows#run-id
"""

WorkflowExecutionSuffixID = Annotated[
    str, StringConstraints(pattern=WF_EXEC_SUFFIX_PATTERN)
]
"""The suffix of a workflow execution ID."""


def generate_exec_id(
    workflow_id: AnyWorkflowID, *, delimiter: str = "/"
) -> WorkflowExecutionID:
    """Inner workflow ID for a run, using the workflow ID and run ID."""
    wf_id = WorkflowUUID.new(workflow_id)
    exec_id = ExecutionUUID.new_uuid4()
    return delimiter.join((wf_id.short(), exec_id.short()))


def exec_suffix_id() -> WorkflowExecutionSuffixID:
    """The suffix of a workflow execution ID."""
    return generate_resource_id(ResourcePrefix.WORKFLOW_EXECUTION)


def exec_id_to_parts(
    wf_exec_id: WorkflowExecutionID,
) -> tuple[WorkflowID, WorkflowExecutionSuffixID]:
    """The components of a workflow execution ID."""
    wf_id, exec_suffix_id = wf_exec_id.split(":", 1)
    return cast(WorkflowID, wf_id), cast(WorkflowExecutionSuffixID, exec_suffix_id)


def exec_id_from_parts(
    wf_id: WorkflowID, exec_suffix_id: WorkflowExecutionSuffixID
) -> WorkflowExecutionID:
    """Create a workflow execution ID from its components."""
    return f"{wf_id}:{exec_suffix_id}"


def wf_id_from_any_dep(workflow_id: AnyWorkflowID) -> WorkflowID:
    """Convert a workflow ID string to a UUID.

    Accepts either a UUID string or a short ID in the format wf_XXXXX.
    """
    return WorkflowUUID.new(workflow_id)


def opt_wf_id_from_any_query_dep(
    workflow_id: AnyWorkflowID | None = Query(None),
) -> WorkflowID | None:
    """Convert a workflow ID string to a UUID.

    Accepts either a UUID string or a short ID in the format wf_XXXXX.
    """
    return WorkflowUUID.new(workflow_id) if workflow_id else None


def wf_id_from_any_query_dep(workflow_id: AnyWorkflowID = Query(...)) -> WorkflowID:
    """Convert a workflow ID string to a UUID.

    Accepts either a UUID string or a short ID in the format wf_XXXXX.
    """
    return WorkflowUUID.new(workflow_id)


AnyWorkflowIDPath = Annotated[WorkflowID, Depends(wf_id_from_any_dep)]
"""A workflow ID that can be either a UUID or a short ID in the format wf_XXXXX."""
OptionalAnyWorkflowIDQuery = Annotated[
    WorkflowID | None, Depends(opt_wf_id_from_any_query_dep)
]
"""An optional workflow ID that can be either a UUID or a short ID in the format wf_XXXXX."""

AnyWorkflowIDQuery = Annotated[WorkflowID, Depends(wf_id_from_any_query_dep)]
