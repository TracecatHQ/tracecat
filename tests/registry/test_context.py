from __future__ import annotations

from tracecat_registry.context import RegistryContext
from tracecat_registry.sdk.client import TracecatClient


def test_registry_context_preserves_positional_executor_url_token_compat() -> None:
    """Adding gateway support must not shift RegistryContext positional slots.

    Registry artifacts can instantiate this dataclass positionally. The gateway
    socket must stay out of this constructor contract so `executor_url` and
    `token` keep their historical slots.
    """
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


def test_registry_context_uses_injected_client() -> None:
    """In-process executor backends can bind SDK calls to their gateway."""
    client = TracecatClient(action_gateway_socket="/tmp/action-gateway.sock")
    context = RegistryContext(
        workspace_id="workspace-id",
        workflow_id="workflow-id",
        run_id="run-id",
        _client=client,
    )

    assert context.client is client
