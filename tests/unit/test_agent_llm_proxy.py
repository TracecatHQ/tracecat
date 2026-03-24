from pathlib import Path

from tracecat.agent.sandbox.llm_proxy import LLMSocketProxy


def test_llm_socket_proxy_uses_configured_default_url(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.sandbox.llm_proxy.get_default_litellm_url",
        lambda: "http://litellm:4000",
    )

    proxy = LLMSocketProxy(socket_path=Path("/tmp/test-llm.sock"))

    assert proxy.litellm_url == "http://litellm:4000"
