from __future__ import annotations

import traceback

from pydantic import UUID4, BaseModel


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

    def __str__(self) -> str:
        return (
            f"{self.type}: {self.message}"
            f"\n\n{'-' * 30}"
            f"\nFile: {self.filename}"
            f"\nFunction: {self.function}"
            f"\nLine: {self.lineno}"
        )

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
