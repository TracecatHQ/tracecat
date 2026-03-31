from __future__ import annotations

from collections.abc import Callable

import pytest

from tracecat.agent.llm_proxy.core import TracecatLLMProxy
from tracecat.agent.llm_proxy.credentials import StaticCredentialResolver


@pytest.fixture
def static_llm_proxy_factory() -> Callable[[dict[str, str] | None], TracecatLLMProxy]:
    def factory(credentials: dict[str, str] | None) -> TracecatLLMProxy:
        return TracecatLLMProxy(
            credential_resolver=StaticCredentialResolver(credentials)
        )

    return factory
