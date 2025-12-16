"""Utility functions for registry actions.

These utilities are copied from the tracecat core package to allow
registry actions to run without importing tracecat.
"""

from tracecat_registry.utils.datetime import (
    coerce_optional_to_date,
    coerce_optional_to_utc_datetime,
    coerce_to_date,
    coerce_to_utc_datetime,
)
from tracecat_registry.utils.exceptions import (
    ExpressionError,
    RegistryError,
)
from tracecat_registry.utils.formatters import (
    tabulate,
    to_html,
    to_markdown_list,
    to_markdown_table,
    to_markdown_tasks,
)
from tracecat_registry.utils.jsonpath import (
    ExprContext,
    eval_jsonpath,
)
from tracecat_registry.utils.safe_lambda import (
    build_safe_lambda,
)

__all__ = [
    # Datetime utilities
    "coerce_to_utc_datetime",
    "coerce_optional_to_utc_datetime",
    "coerce_to_date",
    "coerce_optional_to_date",
    # Formatters
    "tabulate",
    "to_markdown_list",
    "to_markdown_table",
    "to_markdown_tasks",
    "to_html",
    # JSONPath
    "eval_jsonpath",
    "ExprContext",
    # Safe lambda
    "build_safe_lambda",
    # Exceptions
    "ExpressionError",
    "RegistryError",
]
