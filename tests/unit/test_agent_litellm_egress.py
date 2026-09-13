from __future__ import annotations

import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

from tracecat.agent import gateway as _gateway
from tracecat.agent.litellm import build_exec_env
from tracecat.network import DisallowedUrlError
from tracecat.outbound_http import GuardedAsyncHTTPTransport


def test_managed_litellm_provider_clients_use_guarded_transport() -> None:
    assert _gateway.callback_handler is not None
    shared_client = litellm.aclient_session
    assert shared_client is not None
    assert isinstance(shared_client._transport, GuardedAsyncHTTPTransport)
    assert shared_client.follow_redirects is False
    assert shared_client.trust_env is False

    handler = AsyncHTTPHandler(timeout=1.0)
    assert isinstance(handler.client._transport, GuardedAsyncHTTPTransport)
    assert handler.client.follow_redirects is False
    assert handler.client.trust_env is False


@pytest.mark.anyio
async def test_managed_litellm_provider_client_blocks_private_target() -> None:
    handler = AsyncHTTPHandler(timeout=1.0)
    try:
        with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
            await handler.get("http://127.0.0.1:1/v1/models")
    finally:
        await handler.close()


def test_litellm_launcher_disables_aiohttp_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISABLE_AIOHTTP_TRANSPORT", raising=False)

    env = build_exec_env()

    assert env["DISABLE_AIOHTTP_TRANSPORT"] == "True"


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("model", "api_version"),
    [
        pytest.param("openai/test", None, id="openai"),
        pytest.param("anthropic/test", None, id="anthropic"),
        pytest.param("mistral/test", None, id="mistral"),
        pytest.param("hosted_vllm/test", None, id="vllm-and-custom"),
        pytest.param("ollama_chat/test", None, id="ollama"),
        pytest.param("openrouter/test", None, id="openrouter"),
        pytest.param("litellm_proxy/test", None, id="litellm"),
        pytest.param(
            "azure/test",
            "2024-02-01",
            id="azure-openai",
        ),
        pytest.param("azure_ai/test", None, id="azure-ai"),
    ],
)
async def test_litellm_provider_adapters_block_private_api_base(
    model: str,
    api_version: str | None,
) -> None:
    with pytest.raises(Exception) as exc_info:
        await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": "hello"}],
            api_base="http://127.0.0.1:1/v1",
            api_key="secret",
            api_version=api_version,
            max_retries=0,
        )

    assert any(
        isinstance(error, DisallowedUrlError)
        for error in _exception_chain(exc_info.value)
    )
