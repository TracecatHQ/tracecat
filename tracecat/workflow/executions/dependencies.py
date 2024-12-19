import urllib.parse
from typing import Annotated

from fastapi import Depends

from tracecat.workflow.executions.models import WorkflowExecutionID


def unquote_dep(execution_id: WorkflowExecutionID) -> WorkflowExecutionID:
    return urllib.parse.unquote(execution_id)


UnquotedExecutionID = Annotated[WorkflowExecutionID, Depends(unquote_dep)]
"""Dependency for an unquoted execution ID."""
