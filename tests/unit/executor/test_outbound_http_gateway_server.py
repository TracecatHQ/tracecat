from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
from fastapi.testclient import TestClient

from tracecat.executor.outbound_http_gateway_server import (
    OutboundHTTPGatewaySettings,
    create_outbound_http_gateway_app,
)


@contextmanager
def _mock_upstream() -> Any:
    state: dict[str, Any] = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            state["requests"].append(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": raw_body,
                }
            )
            response_body = json.dumps({"forwarded": True}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Upstream", "ok")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format: str, *args: object) -> None:
            _ = format, args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url, state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _dispatch_payload(
    url: str, *, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": url,
        "headers": headers or {"Content-Type": "application/json"},
        "body_base64": base64.b64encode(b'{"hello":"world"}').decode("ascii"),
        "timeout_ms": 5000,
        "follow_redirects": False,
    }


def _mint_executor_token(
    *,
    secret: str,
    subject: str = "tracecat-executor",
    ttl_seconds: int = 300,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": "tracecat-executor",
        "aud": "tracecat-api",
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "workspace_id": "ws_test",
        "user_id": "user_test",
        "service_id": "tracecat-executor",
        "wf_id": "wf_test",
        "wf_exec_id": "wf_exec_test",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_dispatch_simulates_when_requested_by_header() -> None:
    app = create_outbound_http_gateway_app(OutboundHTTPGatewaySettings())
    client = TestClient(app)

    response = client.post(
        "/v1/dev-proxy/dispatch",
        headers={"X-Tracecat-Workflow-Id": "wf_test"},
        json=_dispatch_payload(
            "https://example.com/api",
            headers={
                "Content-Type": "application/json",
                "X-Tracecat-Simulate": "true",
            },
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_code"] == 200
    assert payload["headers"]["X-Tracecat-Gateway-Mode"] == "simulate"
    simulated_body = json.loads(base64.b64decode(payload["body_base64"]))
    assert simulated_body["simulated"] is True
    assert simulated_body["tracecat"]["x-tracecat-workflow-id"] == "wf_test"


def test_dispatch_simulates_for_configured_host() -> None:
    app = create_outbound_http_gateway_app(
        OutboundHTTPGatewaySettings(simulate_hosts=frozenset({"example.com"}))
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dev-proxy/dispatch",
        json=_dispatch_payload("https://example.com/api"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["headers"]["X-Tracecat-Gateway-Mode"] == "simulate"


def test_dispatch_forwards_requests_upstream() -> None:
    with _mock_upstream() as (upstream_url, state):
        app = create_outbound_http_gateway_app(OutboundHTTPGatewaySettings())
        client = TestClient(app)

        response = client.post(
            "/v1/dev-proxy/dispatch",
            json=_dispatch_payload(
                f"{upstream_url}/dispatch",
                headers={
                    "Content-Type": "application/json",
                    "X-Test": "yes",
                    "X-Tracecat-Simulate": "false",
                },
            ),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_code"] == 201
    assert payload["headers"]["X-Tracecat-Gateway-Mode"] == "forward"
    [request] = state["requests"]
    assert request["path"] == "/dispatch"
    assert request["headers"]["X-Test"] == "yes"
    assert "X-Tracecat-Simulate" not in request["headers"]
    assert json.loads(request["body"].decode("utf-8")) == {"hello": "world"}


def test_dispatch_verifies_executor_token() -> None:
    secret = "test-secret"
    app = create_outbound_http_gateway_app(
        OutboundHTTPGatewaySettings(
            verify_executor_token=True,
            executor_token_secret=secret,
            simulate_all=True,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dev-proxy/dispatch",
        headers={"Authorization": f"Bearer {_mint_executor_token(secret=secret)}"},
        json=_dispatch_payload("https://example.com/api"),
    )

    assert response.status_code == 200
    assert response.json()["headers"]["X-Tracecat-Gateway-Mode"] == "simulate"


def test_dispatch_rejects_missing_executor_token() -> None:
    app = create_outbound_http_gateway_app(
        OutboundHTTPGatewaySettings(
            verify_executor_token=True,
            executor_token_secret="test-secret",
            simulate_all=True,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dev-proxy/dispatch",
        json=_dispatch_payload("https://example.com/api"),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_dispatch_rejects_invalid_executor_token() -> None:
    secret = "test-secret"
    app = create_outbound_http_gateway_app(
        OutboundHTTPGatewaySettings(
            verify_executor_token=True,
            executor_token_secret=secret,
            simulate_all=True,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dev-proxy/dispatch",
        headers={
            "Authorization": (
                f"Bearer {_mint_executor_token(secret=secret, subject='wrong-subject')}"
            )
        },
        json=_dispatch_payload("https://example.com/api"),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid executor token"
