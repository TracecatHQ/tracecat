from __future__ import annotations

from tracecat_registry.context import RegistryContext


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
