"""VCS provider exceptions."""

from typing import Any

from tracecat.exceptions import TracecatException


class VcsProviderError(TracecatException):
    """User-facing VCS provider operation error."""

    def __init__(
        self,
        *args: object,
        status_code: int | None = None,
        detail: Any | None = None,
    ) -> None:
        super().__init__(*args, detail=detail)
        self.status_code = status_code
        """HTTP status the provider returned, when the error came from a response."""
