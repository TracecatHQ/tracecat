"""VCS provider exceptions."""

from tracecat.exceptions import TracecatException


class VcsProviderError(TracecatException):
    """User-facing VCS provider operation error."""
