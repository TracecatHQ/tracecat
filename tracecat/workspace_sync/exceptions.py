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


class PushBundleUnreadableError(TracecatValidationError):
    """A partial push cannot keep the other entries of an unreadable bundle."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(
            f"{path} on the target branch is not a valid bundle file, so this "
            "push cannot keep the resources in it. Fix the file on the branch, "
            "or restore it from an earlier commit, then push again."
        )


class PushNameConflictError(TracecatValidationError):
    """A partial push would give two resources in one bundle the same name."""

    def __init__(self, clashes: list[str]) -> None:
        self.clashes = clashes
        super().__init__(
            "These resources on the target branch already use a name this push "
            f"writes: {', '.join(clashes)}. Pull first, or rename the resource "
            "before pushing."
        )


class PushPathConflictError(TracecatValidationError):
    """A partial push would write over a different resource on its branch."""

    def __init__(self, locations: list[str]) -> None:
        self.locations = locations
        super().__init__(
            "These paths already hold a different resource on the target "
            f"branch: {', '.join(locations)}. Pull first, or rename the "
            "resource before pushing."
        )
