"""Tracecat SDK for registry actions.

This SDK provides HTTP clients for accessing Tracecat platform features
from within UDF actions running in sandboxed environments.
"""

from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.cases import CasesClient
from tracecat_registry.sdk.tables import TablesClient
from tracecat_registry.sdk.secrets import SecretsClient
from tracecat_registry.sdk.exceptions import (
    TracecatSDKError,
    TracecatAPIError,
    TracecatAuthError,
    TracecatConflictError,
    TracecatNotFoundError,
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
    "SecretsClient",
    # Exceptions
    "TracecatSDKError",
    "TracecatAPIError",
    "TracecatAuthError",
    "TracecatConflictError",
    "TracecatNotFoundError",
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
