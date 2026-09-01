"""Stable Temporal workflow patch identifiers."""

from enum import StrEnum


class WorkflowPatch(StrEnum):
    """Patch IDs recorded in Temporal workflow histories."""

    ACTION_HEARTBEAT_TIMEOUT_RETRY = "dsl-action-heartbeat-timeout-retry-v1"
    ERROR_OWNER_SEARCH_ATTRIBUTE = "dsl-error-owner-search-attribute-v1"
    ERROR_OWNER_CONTROL_FLOW = "dsl-error-owner-control-flow-v1"
    ERROR_OWNER_AFTER_HANDLER = "dsl-error-owner-after-handler-v1"
    PRESERVE_TEMPORAL_CANCELLATION = "dsl-preserve-temporal-cancellation-v1"
    RUNTIME_ERROR_ATTRIBUTION_INTERCEPTOR = "runtime-error-attribution-interceptor-v1"
