"""Registry test configuration."""

import contextlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager

import httpx
import pytest
from msgraph_core import GraphClientFactory
from tracecat_registry import secrets
from tracecat_registry.integrations import _microsoft_graph_transport as graph_transport

type GraphHandler = Callable[[httpx.Request], httpx.Response]
type InstallGraphTransport = Callable[[GraphHandler], list[httpx.Request]]
type GraphSecrets = Callable[..., AbstractContextManager[None]]


@pytest.fixture
def graph_secrets() -> GraphSecrets:
    """Scope the registry secrets context to the given Microsoft Graph tokens."""

    @contextlib.contextmanager
    def _scoped(**tokens: str) -> Iterator[None]:
        context_token = secrets.set_context(dict(tokens))
        try:
            yield
        finally:
            secrets.reset_context(context_token)

    return _scoped


@pytest.fixture
def install_graph_transport(monkeypatch: pytest.MonkeyPatch) -> InstallGraphTransport:
    """Serve Graph responses from a fake transport under the real middleware.

    Patching the shared transport module covers every registered Microsoft Graph
    SDK wrapper, since they all send through it.
    """

    def _install(handler: GraphHandler) -> list[httpx.Request]:
        requests: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        def _build_http_client(base_url: str) -> httpx.AsyncClient:
            return GraphClientFactory.create_with_default_middleware(
                client=httpx.AsyncClient(transport=httpx.MockTransport(_record))
            )

        monkeypatch.setattr(graph_transport, "build_http_client", _build_http_client)
        return requests

    return _install
