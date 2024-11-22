import urllib.parse
from typing import Annotated

from fastapi import Depends

from tracecat.workflow.executions.models import ExecutionOrScheduleID


def unquote_dep(execution_id: ExecutionOrScheduleID) -> ExecutionOrScheduleID:
    return urllib.parse.unquote(execution_id)


UnquotedExecutionOrScheduleID = Annotated[ExecutionOrScheduleID, Depends(unquote_dep)]
"""Dependency for an unquoted execution or schedule ID."""
