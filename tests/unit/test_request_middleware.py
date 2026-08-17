from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    with TestClient(app, client=("203.0.113.10", 50000)) as client:
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
