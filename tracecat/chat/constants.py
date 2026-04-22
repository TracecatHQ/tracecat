"""Chat-related constants shared across services."""

APPROVAL_REQUEST_HEADER = "Approvals required"
"""Marker text used in assistant messages that defer tool calls for approval."""

APPROVAL_DATA_PART_TYPE = "data-approval-request"
"""UI data part identifier for approval request payloads."""

COMPACTION_DATA_PART_TYPE = "data-compaction"
"""UI data part identifier for transient compaction status payloads."""
