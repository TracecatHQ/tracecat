"""Tracecat SDK for registry actions.

This SDK provides HTTP clients for accessing Tracecat platform features
from within UDF actions running in sandboxed environments.
"""

from tracecat_registry.sdk.cases import CasesClient
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.tables import TablesClient

from tracecat_registry.sdk.exceptions import (
    TracecatAPIError,
    TracecatAuthError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatSDKError,
    TracecatValidationError,
)
from tracecat_registry.sdk.types import (
    UNSET,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    SqlType,
    Unset,
    is_set,
)

__all__ = [
    # Main client
    "TracecatClient",
    # Sub-clients
    "CasesClient",
    "TablesClient",
    # Exceptions
    "TracecatAPIError",
    "TracecatAuthError",
    "TracecatConflictError",
    "TracecatNotFoundError",
    "TracecatSDKError",
    "TracecatValidationError",
    # Sentinel types and helpers
    "UNSET",
    "Unset",
    "is_set",
    # Case types
    "CasePriority",
    "CaseSeverity",
    "CaseStatus",
    # Table types
    "SqlType",
]
