import contextlib
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock, call

import pytest
from tracecat_registry.integrations.agents.builder import run_agent


@contextlib.asynccontextmanager
async def patched_model_config(
    model_name: str, provider: str
) -> AsyncIterator[dict[str, list[dict[str, str | None]]]]:
    # Patch get_model to inspect the base_url passed
    from tracecat_registry.integrations import pydantic_ai as mod

    original_get_model = mod.get_model
    spy: dict[str, list[dict[str, str | None]]] = {"calls": []}

    def spy_get_model(name: str, prov: str, base_url: str | None = None):
        spy["calls"].append({"name": name, "provider": prov, "base_url": base_url})

        # Return a minimal stub for build_agent to accept; we patch build_agent too
        class DummyModel:  # noqa: D401
            """Dummy pydantic-ai model placeholder"""

        return DummyModel()

    try:
        mod.get_model = spy_get_model
        yield spy
    finally:
        mod.get_model = original_get_model


@pytest.mark.anyio
async def test_run_agent_uses_env_base_url_override(monkeypatch):
    # Arrange: patch env and intercept model creation
    monkeypatch.setenv("TRACECAT__LLM_BASE_URL", "https://proxy.example/v1")

    # Patch build_agent to return a stub agent with minimal async iteration API
    from tracecat_registry.integrations import pydantic_ai as mod

    class DummyRun:
        def __init__(self):
            self._yielded = False
            self.result = Mock()
            self.result.output = "ok"
            self.result.all_messages = lambda: []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True

            class DummyNode:  # noqa: D401
                """End node only"""

                @staticmethod
                def is_end():
                    return True

            return DummyNode()

    class DummyAgent:
        async def iter(self, *args, **kwargs):
            return DummyRun()

    monkeypatch.setattr(mod, "build_agent", lambda **kwargs: DummyAgent())

    async with patched_model_config("gpt-4o-mini", "openai") as spy:
        await run_agent(
            user_prompt="hi",
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=["core.http_request"],
        )

    assert spy["calls"], "get_model was not called"
    assert spy["calls"][0]["base_url"] == "https://proxy.example/v1"


@pytest.mark.anyio
async def test_run_agent_merges_env_fixed_arguments_and_injects_correctly(
    monkeypatch, mocker
):
    # Set env fixed arguments
    monkeypatch.setenv(
        "TRACECAT__AGENT_FIXED_ARGUMENTS",
        '{"core.cases.create_case": {"priority": "high"}}',
    )

    # Patch registry actions service
    from tracecat_registry.integrations.agents import builder as bmod

    mock_ra1 = Mock()
    mock_ra1.namespace = "core.cases"
    mock_ra1.name = "create_case"
    mock_ra2 = Mock()
    mock_ra2.namespace = "tools.slack"
    mock_ra2.name = "post_message"

    mock_service = Mock()
    mock_service.get_actions = AsyncMock(return_value=[mock_ra1, mock_ra2])
    mock_service.list_actions = AsyncMock(return_value=[mock_ra1, mock_ra2])
    mock_service.fetch_all_action_secrets = AsyncMock(return_value=[])

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service
    mocker.patch.object(
        bmod.RegistryActionsService, "with_session", return_value=mock_ctx
    )

    # Spy on create_tool_from_registry
    create_tool_stub = mocker.patch.object(
        bmod, "create_tool_from_registry", return_value=Mock()
    )

    # Patch build_agent to avoid running a real agent
    from tracecat_registry.integrations import pydantic_ai as pmod

    class DummyRun:
        def __init__(self):
            self._yielded = False
            self.result = Mock()
            self.result.output = "ok"
            self.result.all_messages = lambda: []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True

            class DummyNode:  # noqa: D401
                """End node only"""

                @staticmethod
                def is_end():
                    return True

            return DummyNode()

    class DummyAgent:
        async def iter(self, *args, **kwargs):
            return DummyRun()

    monkeypatch.setattr(pmod, "build_agent", lambda **kwargs: DummyAgent())

    # Act
    await run_agent(
        user_prompt="hi",
        model_name="gpt-4o-mini",
        model_provider="openai",
        actions=["core.cases.create_case", "tools.slack.post_message"],
    )

    # Assert: fixed arguments injected only for action with override
    expected_calls = [
        call("core.cases.create_case", {"priority": "high"}),
        call("tools.slack.post_message"),
    ]
    create_tool_stub.assert_has_calls(expected_calls, any_order=True)
