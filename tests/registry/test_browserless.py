"""Egress boundary tests for the Browserless browser actions.

Every action hands a caller-supplied target URL to a browser that Tracecat does
not share a network stack with, so the guard in
`tracecat_registry._internal.network` is the only place that boundary exists.
These tests assert on that boundary, not on the Browserless request or response
schema: the fake transport only records whether a request was allowed to leave.
"""

import contextlib
from collections.abc import Callable, Iterator
from ipaddress import ip_network
from typing import Any

import httpx
import pytest
from tracecat_registry import config, secrets
from tracecat_registry._internal.network import DisallowedTargetUrlError
from tracecat_registry.integrations import browserless

type Action = Callable[..., Any]

# Directly addresses a host that is not on the public internet. Each of these is
# reachable from a browser deployed inside a private network or a cloud VM.
BLOCKED_TARGETS = [
    "http://127.0.0.1/",
    "http://localhost/admin",
    "http://[::1]:8080/",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://[fd00:ec2::254]/latest/meta-data/",
    "http://[::ffff:169.254.169.254]/latest/meta-data/",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://100.64.0.1/",
    "http://0.0.0.0/",
    "http://2130706433/",
    "http://0x7f000001/",
]

REJECTED_URLS = [
    "file:///etc/passwd",
    "ftp://1.1.1.1/",
    "gopher://1.1.1.1/",
    "//1.1.1.1/",
    "http:///no-host",
    "http://user:password@1.1.1.1/",
    "http://1.1.1.1:99999/",
]

# Publicly routable literal, so the allowed case needs no DNS either.
PUBLIC_TARGET = "http://1.1.1.1/status"

# A caller-supplied base_url carries the API token in its Authorization header,
# so it must clear the same egress policy as the page being opened: an endpoint
# on a private network, the cloud metadata address, or an attacker-controlled
# host would otherwise receive the credential.
BLOCKED_BASE_URLS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000/",
    "http://169.254.169.254",
    "http://[::ffff:169.254.169.254]",
    "http://10.0.0.5:3000",
    "http://192.168.1.10",
    "file:///etc/passwd",
    "ftp://1.1.1.1/",
    "http://user:password@1.1.1.1/",
]

# Publicly routable literal, so the allowed case needs no DNS.
PUBLIC_BASE_URL = "http://1.1.1.1:3000"

TEST_TOKEN = "test-token"


@contextlib.contextmanager
def browserless_token() -> Iterator[None]:
    """Scope the registry secrets context to a Browserless token.

    The context is a contextvar, so it has to be entered from inside the test
    coroutine rather than from a synchronous fixture.
    """
    token = secrets.set_context({"BROWSERLESS_TOKEN": TEST_TOKEN})
    try:
        yield
    finally:
        secrets.reset_context(token)


def _call(action: Action, url: str, **kwargs: Any) -> Any:
    match action:
        case browserless.scrape_elements:
            return action(url=url, elements=[{"selector": "h1"}], **kwargs)
        case _:
            return action(url=url, **kwargs)


@pytest.fixture(params=["get_content", "take_screenshot", "scrape_elements"])
def action(request: pytest.FixtureRequest) -> Action:
    return getattr(browserless, request.param)


@pytest.fixture
def sent_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Record every request an action manages to send, and serve a stub reply."""
    requests: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html></html>"
        )

    real_client = httpx.AsyncClient

    def _fake_client(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(_record), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake_client)
    return requests


@pytest.mark.anyio
@pytest.mark.parametrize("url", BLOCKED_TARGETS)
async def test_private_target_never_reaches_the_browser(
    action: Action, url: str, sent_requests: list[httpx.Request]
) -> None:
    with browserless_token(), pytest.raises(DisallowedTargetUrlError):
        await _call(action, url)
    assert sent_requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("url", REJECTED_URLS)
async def test_unusable_target_url_never_reaches_the_browser(
    action: Action, url: str, sent_requests: list[httpx.Request]
) -> None:
    with browserless_token(), pytest.raises(DisallowedTargetUrlError):
        await _call(action, url)
    assert sent_requests == []


@pytest.mark.anyio
async def test_public_target_is_sent(
    action: Action, sent_requests: list[httpx.Request]
) -> None:
    with browserless_token():
        await _call(action, PUBLIC_TARGET)
    assert len(sent_requests) == 1
    request = sent_requests[0]
    assert PUBLIC_TARGET in request.content.decode()
    # The token is a Bearer credential, never a query parameter that would be
    # written to an access log or leak through a referrer.
    assert request.headers["authorization"] == f"Bearer {TEST_TOKEN}"
    assert TEST_TOKEN not in str(request.url)


@pytest.mark.anyio
async def test_operator_allowlisted_cidr_is_sent(
    action: Action,
    sent_requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An intentional private target stays reachable when an operator allows it."""
    monkeypatch.setattr(
        config,
        "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS",
        (ip_network("10.0.0.0/24"),),
    )
    with browserless_token():
        await _call(action, "http://10.0.0.5/")
        assert len(sent_requests) == 1
        # A private address outside the configured CIDR is still refused.
        with pytest.raises(DisallowedTargetUrlError):
            await _call(action, "http://10.0.1.5/")
    assert len(sent_requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", BLOCKED_BASE_URLS)
async def test_untrusted_base_url_never_receives_the_token(
    action: Action, base_url: str, sent_requests: list[httpx.Request]
) -> None:
    """A base_url that fails the egress policy is refused before the token is sent."""
    with browserless_token(), pytest.raises(DisallowedTargetUrlError):
        await _call(action, PUBLIC_TARGET, base_url=base_url)
    assert sent_requests == []


@pytest.mark.anyio
async def test_public_base_url_is_sent(
    action: Action, sent_requests: list[httpx.Request]
) -> None:
    """A publicly routable base_url is accepted and receives the request."""
    with browserless_token():
        await _call(action, PUBLIC_TARGET, base_url=PUBLIC_BASE_URL)
    assert len(sent_requests) == 1
    request = sent_requests[0]
    assert str(request.url).startswith(PUBLIC_BASE_URL)
    assert request.headers["authorization"] == f"Bearer {TEST_TOKEN}"


@pytest.mark.anyio
async def test_operator_allowlisted_base_url_is_sent(
    action: Action,
    sent_requests: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted instance on an allowed private CIDR stays reachable."""
    monkeypatch.setattr(
        config,
        "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS",
        (ip_network("10.0.0.0/24"),),
    )
    with browserless_token():
        await _call(action, PUBLIC_TARGET, base_url="http://10.0.0.5:3000")
        assert len(sent_requests) == 1
        # An instance outside the configured CIDR is still refused.
        with pytest.raises(DisallowedTargetUrlError):
            await _call(action, PUBLIC_TARGET, base_url="http://10.0.1.5:3000")
    assert len(sent_requests) == 1
