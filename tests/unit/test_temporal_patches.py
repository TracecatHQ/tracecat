from tracecat.temporal.patches import WorkflowPatch


def test_workflow_patch_ids_are_history_stable() -> None:
    assert {patch.name: patch.value for patch in WorkflowPatch} == {
        "ACTION_HEARTBEAT_TIMEOUT_RETRY": "dsl-action-heartbeat-timeout-retry-v1",
        "ERROR_OWNER_SEARCH_ATTRIBUTE": "dsl-error-owner-search-attribute-v1",
        "ERROR_OWNER_CONTROL_FLOW": "dsl-error-owner-control-flow-v1",
        "ERROR_OWNER_AFTER_HANDLER": "dsl-error-owner-after-handler-v1",
        "PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE": (
            "dsl-preserve-original-error-after-handler-failure-v1"
        ),
        "PRESERVE_TEMPORAL_CANCELLATION": "dsl-preserve-temporal-cancellation-v1",
        "RUNTIME_ERROR_ATTRIBUTION_INTERCEPTOR": (
            "runtime-error-attribution-interceptor-v1"
        ),
    }
