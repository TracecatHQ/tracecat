"""Tests for the lightweight single-turn LLM completion helper."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
import respx

from tracecat.agent.common.config import TRACECAT__LITELLM_BASE_URL
from tracecat.agent.llm import LLMCompletionError, complete
from tracecat.agent.schemas import DefaultModelSelection
from tracecat.auth.types import Role

LLM = "tracecat.agent.llm"
COMPLETIONS_URL = f"{TRACECAT__LITELLM_BASE_URL.rstrip('/')}/chat/completions"


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )


@pytest.fixture
def default_model() -> Iterator[MagicMock]:
    management = MagicMock()
    management.get_default_model_selection = AsyncMock()
    management.get_catalog_credentials = AsyncMock(return_value={"API_KEY": "x"})
    catalog = MagicMock()
    catalog.resolve_model_replacement = AsyncMock(return_value=None)
    with (
        patch(f"{LLM}.AgentManagementService", return_value=management),
        patch(f"{LLM}.AgentCatalogService", return_value=catalog),
        patch(f"{LLM}.resolve_gateway_provider_config", return_value=None),
        patch(f"{LLM}.mint_llm_token", return_value="token"),
    ):
        yield management.get_default_model_selection


def _select(default_model: MagicMock, provider: str, model: str) -> None:
    default_model.return_value = DefaultModelSelection(
        catalog_id=uuid.uuid4(), model_name=model, model_provider=provider
    )


def _completion(content: str | None, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": content}, "finish_reason": finish_reason}
            ]
        },
    )


async def _complete(role: Role) -> str:
    return await complete(
        prompt="Users cannot sign in",
        system_prompt="Title it",
        session=AsyncMock(),
        role=role,
        max_tokens=1024,
        reasoning_effort="low",
    )


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    ("provider", "expected"),
    [("openai", "low"), ("azure_openai", "low"), ("anthropic", None)],
)
async def test_reasoning_effort_is_sent_only_to_openai_family(
    role: Role, default_model: MagicMock, provider: str, expected: str | None
) -> None:
    _select(default_model, provider, "some-model")
    route = respx.post(COMPLETIONS_URL).mock(return_value=_completion("Login failures"))

    assert await _complete(role) == "Login failures"

    body = orjson.loads(route.calls.last.request.content)
    assert body["max_tokens"] == 1024
    assert body.get("reasoning_effort") == expected


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("content", [None, "", "  \n"])
async def test_reply_without_text_names_model_and_finish_reason(
    role: Role, default_model: MagicMock, content: str | None
) -> None:
    _select(default_model, "openai", "reasoning-model")
    respx.post(COMPLETIONS_URL).mock(
        return_value=_completion(content, finish_reason="length")
    )

    with pytest.raises(
        LLMCompletionError,
        match=r"model=reasoning-model, finish_reason=length",
    ):
        await _complete(role)
