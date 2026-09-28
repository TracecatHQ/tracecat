"""Exception-chain traversal shared by attribution and telemetry."""

from collections.abc import Iterator

from temporalio.exceptions import FailureError


def iter_error_chain(
    error: BaseException,
    *,
    include_implicit_context: bool = True,
) -> Iterator[BaseException]:
    """Walk an exception chain once, following Temporal and Python causes.

    Args:
        error: The exception to start from.
        include_implicit_context: Whether to traverse Python's incidental
            ``__context__`` chain in addition to Temporal and explicit causes.
    """
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None:
        current_id = id(current)
        if current_id in seen:
            return
        seen.add(current_id)
        yield current

        if isinstance(current, FailureError) and isinstance(
            current.cause, BaseException
        ):
            current = current.cause
        elif isinstance(current.__cause__, BaseException):
            current = current.__cause__
        elif (
            include_implicit_context
            and not current.__suppress_context__
            and isinstance(current.__context__, BaseException)
        ):
            current = current.__context__
        else:
            current = None
