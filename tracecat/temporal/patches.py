"""Stable Temporal workflow patch identifiers."""

from enum import StrEnum, unique


@unique
class WorkflowPatch(StrEnum):
    """Patch IDs recorded in Temporal workflow histories."""

    ACTION_HEARTBEAT_TIMEOUT_RETRY = "dsl-action-heartbeat-timeout-retry-v1"
    ERROR_OWNER_SEARCH_ATTRIBUTE = "dsl-error-owner-search-attribute-v1"
    ERROR_OWNER_CONTROL_FLOW = "dsl-error-owner-control-flow-v1"
    ERROR_OWNER_AFTER_HANDLER = "dsl-error-owner-after-handler-v1"
    PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE = (
        "dsl-preserve-original-error-after-handler-failure-v1"
    )
    PRESERVE_TEMPORAL_CANCELLATION = "dsl-preserve-temporal-cancellation-v1"
    RUNTIME_ERROR_ATTRIBUTION_INTERCEPTOR = "runtime-error-attribution-interceptor-v1"


@unique
class DurableAgentWorkflowPatch(StrEnum):
    """Stable patch IDs recorded in durable agent workflow histories.

    Never reuse IDs. Retain branches until old histories have aged out, then
    deprecate the marker before removing it in a later release.
    """

    BUILD_AGENT_TOOL_DEFINITIONS = (
        "tracecat_ee.agent.workflows.durable.build_agent_tool_definitions"
    )
    EMIT_PRE_STREAM_SESSION_ERRORS = (
        "tracecat_ee.agent.workflows.durable.emit_pre_stream_session_errors"
    )
    PERSIST_SESSION_ERROR = "tracecat_ee.agent.workflows.durable.persist_session_error"
    AGENT_REQUEST_CANCEL = "durable-agent-request-cancel-v1"
    UPSERT_TRACECAT_SEARCH_ATTRIBUTES = (
        "durable-agent-upsert-tracecat-search-attributes-v1"
    )
    LOAD_TERMINAL_MESSAGE_HISTORY = "durable-agent-load-terminal-message-history-v1"
    PRESERVE_RESUMED_AGENT_BINDINGS = "durable-agent-preserve-resumed-agent-bindings-v1"
    RESOLVE_AGENTS_PER_TURN = "durable-agent-resolve-agents-per-turn-v1"
    FINALIZE_TURN = "durable-agent-finalize-turn-v1"
    FINALIZE_TURN_WITH_END = "durable-agent-finalize-turn-with-end-v1"
    REMINT_SCOPE_TOKENS = "durable-agent-remint-scope-tokens-v1"
    APPROVAL_STREAM_V2 = "durable-agent-approval-stream-v2"
