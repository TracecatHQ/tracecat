WF_FAILURE_REF = "__workflow_failure__"
"""Sentinel constant for workflow-level failures in compact events."""

WF_COMPLETED_REF = "__workflow_completed__"
"""Sentinel constant for workflow-level completion in compact events."""

WF_EXECUTION_MEMO_REGISTRY_LOCK_KEY = "registry_lock"
"""Memo key containing the execution registry lock payload."""

WF_EXECUTION_MEMO_DEFINITION_VERSION_KEY = "workflow_definition_version"
"""Memo key containing the committed workflow definition version (if available)."""
