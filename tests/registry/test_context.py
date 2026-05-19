from __future__ import annotations

from tracecat_registry.context import RegistryContext


def test_registry_context_preserves_positional_executor_url_token_compat() -> None:
    context = RegistryContext(
        "workspace-id",
        "workflow-id",
        "run-id",
        "workflow-exec-id",
        "prod",
        "http://api:8000",
        "http://executor:8000",
        "executor-token",
    )

    assert context.wf_exec_id == "workflow-exec-id"
    assert context.environment == "prod"
    assert context.api_url == "http://api:8000"
    assert context.executor_url == "http://executor:8000"
    assert context.token == "executor-token"
    assert context.action_gateway_socket is None


def test_registry_context_accepts_action_gateway_socket_as_keyword_only() -> None:
    context = RegistryContext(
        "workspace-id",
        "workflow-id",
        "run-id",
        action_gateway_socket="/var/run/tracecat/action-gateway.sock",
    )

    assert context.action_gateway_socket == "/var/run/tracecat/action-gateway.sock"
