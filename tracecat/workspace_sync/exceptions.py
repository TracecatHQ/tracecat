"""Typed, safe failures returned by Git export operations."""

from tracecat.exceptions import TracecatValidationError
from tracecat.workspace_sync.schemas import WorkflowExportDiagnostic

MAX_EXPORT_DIAGNOSTICS = 50


class WorkflowExportValidationError(TracecatValidationError):
    """An entire export was rejected because workflows could not be exported."""

    def __init__(self, diagnostics: list[WorkflowExportDiagnostic]) -> None:
        ordered = sorted(
            diagnostics, key=lambda item: (str(item.workflow_id), item.code)
        )
        self.diagnostics = ordered[:MAX_EXPORT_DIAGNOSTICS]
        self.omitted_count = max(0, len(ordered) - MAX_EXPORT_DIAGNOSTICS)
        message = "; ".join(
            f"{item.workflow_title}: {item.message}" for item in self.diagnostics
        )
        if self.omitted_count:
            message += f"; {self.omitted_count} additional error(s) omitted"
        super().__init__(message)
