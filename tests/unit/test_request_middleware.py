from ipaddress import ip_network

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat import config
from tracecat.contexts import ctx_request_audit
from tracecat.middleware.request import RequestLoggingMiddleware


def _read_audit_context() -> dict[str, str | None]:
    audit = ctx_request_audit.get()
    return {
        "client_ip": audit.client_ip if audit is not None else None,
        "user_agent": audit.user_agent if audit is not None else None,
    }


def test_request_context_values_are_normalized() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_context() -> dict[str, str | None]:
        return _read_audit_context()

    app.add_api_route("/", read_context)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app, client=("10.0.0.5", 50000)) as client:
        response = client.get(
            "/",
            headers={
                "X-Forwarded-For": "198.51.100.20",
                "User-Agent": (
                    "TracecatClient/1.0 session=synthetic-opaque-value "
                    "Authorization: Bearer synthetic-token"
                ),
            },
        )

    assert response.json() == {
        "client_ip": "198.51.100.20",
        "user_agent": "tracecat/1.0",
    }


@pytest.mark.parametrize(
    ("peer", "forwarded_for", "expected"),
    [
        # Trusted peer: rightmost untrusted hop wins; the client-sent
        # leftmost entry is ignored.
        ("10.0.0.5", "6.6.6.6, 203.0.113.7", "203.0.113.7"),
        # Untrusted peer talking directly: its spoofed header is ignored.
        ("203.0.113.10", "6.6.6.6", "203.0.113.10"),
        # Fully trusted chain (internal traffic): leftmost hop attributed.
        ("10.0.0.5", "10.0.8.4", "10.0.8.4"),
    ],
)
def test_client_ip_resolution_walks_trusted_proxies(
    peer: str, forwarded_for: str, expected: str
) -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_client_ip() -> dict[str, str | None]:
        return {"client_ip": _read_audit_context()["client_ip"]}

    app.add_api_route("/", read_client_ip)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app, client=(peer, 50000)) as client:
        response = client.get("/", headers={"X-Forwarded-For": forwarded_for})

    assert response.json() == {"client_ip": expected}


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (
            "Mozilla/5.0 Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            "edge/131.0.0.0",
        ),
        (
            "Mozilla/5.0 AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "chrome/131.0.0.0",
        ),
        ("Mozilla/5.0 Firefox/133.0", "firefox/133.0"),
        (
            "Mozilla/5.0 Version/18.0 Safari/605.1.15",
            "safari/18.0",
        ),
        (
            "mozilla/5.0 version/18.0 safari/605.1.15",
            "safari/18.0",
        ),
        ("Mozilla/5.0 SyntheticBrowser/1.0", "browser/other"),
    ],
)
def test_request_context_identifies_browser(user_agent: str, expected: str) -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_user_agent() -> dict[str, str | None]:
        return {"user_agent": _read_audit_context()["user_agent"]}

    app.add_api_route("/", read_user_agent)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app) as client:
        response = client.get("/", headers={"User-Agent": user_agent})

    assert response.json() == {"user_agent": expected}


def test_request_context_reduces_unknown_user_agent() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_user_agent() -> dict[str, str | None]:
        return {"user_agent": _read_audit_context()["user_agent"]}

    app.add_api_route("/", read_user_agent)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app) as client:
        response = client.get(
            "/",
            headers={"User-Agent": "UnknownClient session=synthetic-opaque-value"},
        )

    assert response.json() == {"user_agent": "other"}


def test_client_ip_resolution_honors_custom_trusted_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A narrowed trust list stops private-range peers from speaking for clients."""
    monkeypatch.setattr(
        config,
        "TRACECAT__AUDIT_TRUSTED_PROXY_CIDRS",
        (ip_network("198.51.100.0/24"),),
    )
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_client_ip() -> dict[str, str | None]:
        return {"client_ip": _read_audit_context()["client_ip"]}

    app.add_api_route("/", read_client_ip)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app, client=("198.51.100.7", 50000)) as client:
        trusted = client.get("/", headers={"X-Forwarded-For": "203.0.113.9"})
    with TestClient(app, client=("10.0.0.5", 50000)) as client:
        untrusted = client.get("/", headers={"X-Forwarded-For": "203.0.113.9"})

    assert trusted.json() == {"client_ip": "203.0.113.9"}
    assert untrusted.json() == {"client_ip": "10.0.0.5"}


def test_invalid_forwarded_ip_falls_back_to_socket_peer() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_client_ip() -> dict[str, str | None]:
        return {"client_ip": _read_audit_context()["client_ip"]}

    app.add_api_route("/", read_client_ip)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app, client=("203.0.113.10", 50000)) as client:
        response = client.get("/", headers={"X-Forwarded-For": "not-an-ip"})

    assert response.json() == {"client_ip": "203.0.113.10"}


def test_no_forwarded_header_and_invalid_socket_peer_yields_none() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_client_ip() -> dict[str, str | None]:
        return {"client_ip": _read_audit_context()["client_ip"]}

    app.add_api_route("/", read_client_ip)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app, client=("not-an-ip", 50000)) as client:
        response = client.get("/")

    assert response.json() == {"client_ip": None}
