"""Workflow identifiers."""

from typing import Annotated

from pydantic import StringConstraints

from tracecat.identifiers.resource import gen_resource_id

WorkflowID = Annotated[str, StringConstraints(pattern=r"wf-[0-9a-f]{32}")]
"""A unique ID for a workflow. e.g. 'wf-77932a0b140a4465a1a25a5c95edcfb8'"""

WorkflowRunID = Annotated[str, StringConstraints(pattern=r"wf-[0-9a-f]{32}:run-[\w-]+")]
"""A unique ID for a workflow run.
    Examples:
     - 'wf-1234567890abcdef1234567890abcdef:run-140a425a577932a0c95edcfb8465a1a'
"""


def run_id(workflow_id: str) -> WorkflowRunID:
    """Inner workflow ID for a run, using the workflow ID and run ID."""
    run_id = gen_resource_id("run")
    return f"{workflow_id}:{run_id}"
