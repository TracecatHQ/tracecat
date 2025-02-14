from collections.abc import Callable
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from functools import wraps

from temporalio import workflow

# Define a context variable to store the datetime offset
wf_time_offset: ContextVar[timedelta | None] = ContextVar("workflow_time_offset")


def adjusted_workflow_time[T: datetime | date](
    func: Callable[..., T],
) -> Callable[..., T]:
    """
    Decorator that adjusts the datetime/date returned by the decorated function
    when running inside a DSLWorkflow by subtracting a configured time offset.

    Args:
        func: Function that returns a datetime or date

    Returns:
        Wrapped function that returns an adjusted datetime/date when in a DSLWorkflow
    """

    @wraps(func)
    def wrapper() -> T:
        result = func()

        # Check if we're in a workflow
        try:
            info = workflow.info()
        except Exception:
            # Not in a workflow, return original time
            return result

        # Only apply offset in DSLWorkflow
        if info.workflow_type == "DSLWorkflow" and (offset := wf_time_offset.get()):
            if isinstance(result, datetime):
                return result - offset
            elif isinstance(result, date):
                # Convert timedelta to days for date objects
                return result - timedelta(days=offset.days)
        return result

    return wrapper
