from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat.contexts import ctx_client_ip, ctx_user_agent
from tracecat.middleware.request import RequestLoggingMiddleware


def test_request_context_values_are_normalized() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_context() -> dict[str, str | None]:
        return {
            "client_ip": ctx_client_ip.get(),
            "user_agent": ctx_user_agent.get(),
        }

    app.add_api_route("/", read_context)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app) as client:
        response = client.get(
            "/",
            headers={
                "X-Forwarded-For": "2001:0db8::1, 192.0.2.1",
                "User-Agent": (
                    "TracecatClient/1.0 user@example.com "
                    "Authorization: Bearer synthetic-token"
                ),
            },
        )

    assert response.json() == {
        "client_ip": "2001:db8::1",
        "user_agent": ("TracecatClient/1.0 [redacted email] Authorization: [redacted]"),
    }


def test_invalid_forwarded_ip_is_not_stored() -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    async def read_client_ip() -> dict[str, str | None]:
        return {"client_ip": ctx_client_ip.get()}

    app.add_api_route("/", read_client_ip)
    app.state.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None})()
    with TestClient(app) as client:
        response = client.get("/", headers={"X-Forwarded-For": "not-an-ip"})

    assert response.json() == {"client_ip": None}
