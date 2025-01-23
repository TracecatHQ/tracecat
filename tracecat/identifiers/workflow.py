"""Workflow identifiers."""

from __future__ import annotations

from typing import Annotated, cast

from pydantic import (
    UUID4,
    StringConstraints,
)

from tracecat.identifiers.common import TracecatUUID
from tracecat.identifiers.resource import ResourcePrefix, generate_resource_id
from tracecat.identifiers.schedules import SCHEDULE_EXEC_ID_PATTERN

# Patterns
WF_ID_PATTERN = r"wf-[0-9a-f]{32}"
EXEC_ID_PATTERN = r"exec-[\w-]+"
WF_EXEC_SUFFIX_PATTERN = rf"({EXEC_ID_PATTERN}|{SCHEDULE_EXEC_ID_PATTERN})"
WF_EXEC_ID_PATTERN = rf"{WF_ID_PATTERN}:{WF_EXEC_SUFFIX_PATTERN}"
WF_ID_PREFIX = "wf_"
WF_ID_SHORT_PATTERN = rf"{WF_ID_PREFIX}[0-9a-zA-Z]+"
WorkflowIDShort = Annotated[str, StringConstraints(pattern=WF_ID_SHORT_PATTERN)]
"""A short base62 encoded string representation of a workflow UUID.

Examples
--------
- long ->  `8d3885a9-6470-4ee0-9d4d-d507cc97393d`
- short -> `wf_4itKqkgCZrLhgYiq5L211X`
"""


class WorkflowUUID(TracecatUUID[WorkflowIDShort]):
    """UUID for workflow resources."""

    prefix = WF_ID_PREFIX
    legacy_prefix = "wf-"


# Annotations
WorkflowID = Annotated[str, StringConstraints(pattern=WF_ID_PATTERN)]
"""A unique ID for a workflow.

This is the logical equivalent of a workflow definition ID in Temporal.

Exapmles
--------
- `wf-77932a0b140a4465a1a25a5c95edcfb8`


References
----------
See Temporal docs: https://docs.temporal.io/workflows#workflow-id
"""


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


def generate_exec_id(workflow_id: WorkflowID) -> WorkflowExecutionID:
    """Inner workflow ID for a run, using the workflow ID and run ID."""
    exec_id = generate_resource_id(ResourcePrefix.WORKFLOW_EXECUTION)
    return f"{workflow_id}:{exec_id}"


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
