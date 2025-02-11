from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from typing import Any

from pydantic import UUID4, BaseModel

from tracecat.config import TRACECAT__APP_ENV
from tracecat.expressions.common import ExprContext
from tracecat.git import GitUrl
from tracecat.parse import to_flat_dict
from tracecat.types.auth import Role


class ExecutorSyncInput(BaseModel):
    repository_id: UUID4


class ExecutorActionErrorInfo(BaseModel):
    """An error that occurred in the registry."""

    action_name: str
    """Name of the action that failed."""

    type: str
    """Type of the error."""

    message: str
    """Error message."""

    filename: str
    """File where the error occurred."""

    function: str
    """Function where the error occurred."""

    lineno: int | None = None
    """Line number where the error occurred."""

    loop_iteration: int | None = None
    """Iteration number of the loop that caused the error."""

    loop_vars: dict[str, Any] | None = None
    """Variables of the loop that caused the error."""

    def __str__(self) -> str:
        parts = []
        msg = f"\n{self.type}: {self.message}"
        if self.loop_iteration is not None:
            flattened = to_flat_dict(
                self.loop_vars or {}, prefix=ExprContext.LOCAL_VARS
            )
            parts.append(
                f"\n[for_each] (Iteration {self.loop_iteration})"
                f"\n\nLoop variables:\n```\n{json.dumps(flattened, indent=2)}\n```"
                f"\n\n{msg}"
                "\n\nPlease ensure that the loop is iterable and that the loop variable has the correct type."
            )
        else:
            parts.append(msg)
        if TRACECAT__APP_ENV == "development":
            parts.append(
                f"\n\n{'-' * 30}"
                f"\nFile: {self.filename}"
                f"\nFunction: {self.function}"
                f"\nLine: {self.lineno}"
            )
        return "\n".join(parts)

    @staticmethod
    def from_exc(e: Exception, action_name: str) -> ExecutorActionErrorInfo:
        """Create an error info from an exception."""
        tb = traceback.extract_tb(e.__traceback__)[-1]  # Get the last frame
        return ExecutorActionErrorInfo(
            action_name=action_name,
            type=e.__class__.__name__,
            message=str(e),
            filename=tb.filename,
            function=tb.name,
            lineno=tb.lineno,
        )


@dataclass
class DispatchActionContext:
    role: Role
    ssh_command: str | None = None
    git_url: GitUrl | None = None
