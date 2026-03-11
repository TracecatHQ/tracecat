from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import sysconfig
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import aiohttp
import httpx
import pytest

from tracecat.executor import (
    outbound_http_gateway_bootstrap as outbound_http_gateway_bootstrap_module,
)
from tracecat.executor import (
    outbound_http_gateway_sitecustomize as outbound_http_gateway_sitecustomize_module,
)


def _resolve_site_packages_path(paths: list[str]) -> str | None:
    if site_packages := next(
        (path for path in paths if "site-packages" in path),
        None,
    ):
        return site_packages
    for key in ("purelib", "platlib"):
        if candidate := sysconfig.get_path(key):
            return candidate
    return None


_SITE_PACKAGES = _resolve_site_packages_path(sys.path)


async def _count_event_loop_ticks(awaitable: Any) -> tuple[Any, int]:
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    try:
        result = await awaitable
    finally:
        stop.set()
        await ticker_task
    return result, ticks


def test_resolve_site_packages_path_falls_back_to_sysconfig() -> None:
    fallback = sysconfig.get_path("purelib") or sysconfig.get_path("platlib")
    assert _resolve_site_packages_path([]) == fallback


@contextmanager
def _mock_gateway() -> Any:
    """Start a local HTTP server that mimics the outbound HTTP gateway.

    Yields a tuple of (base_url, state) where state["requests"] collects
    every incoming POST for later assertion.
    """
    state: dict[str, Any] = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
            state["requests"].append(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "payload": payload,
                }
            )
            response = {
                "status_code": 200,
                "headers": {"Content-Type": "application/json", "X-Gateway": "ok"},
                "body_base64": base64.b64encode(b'{"ok":true}').decode("ascii"),
                "url": payload["url"],
                "reason_phrase": "OK",
            }
            encoded = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

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


@contextmanager
def _mock_json_server() -> Any:
    """Start a simple JSON server used to assert bypass behavior."""
    state: dict[str, Any] = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state["requests"].append(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                }
            )
            response = json.dumps({"direct": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

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


def _bootstrap_env(gateway_url: str) -> dict[str, str]:
    """Build an environment dict with all outbound gateway env vars."""
    env = os.environ.copy()
    env.update(
        {
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_ENABLED": "1",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_URL": gateway_url,
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_AUTH_TOKEN": "executor-token",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_WORKSPACE_ID": str(uuid.uuid4()),
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_ORGANIZATION_ID": str(uuid.uuid4()),
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_ACTION_NAME": "core.http_request",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_ACTION_REF": "call_api",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_BACKEND": "direct",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_SOURCE": "workflow",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_WORKFLOW_ID": "wf_test",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_RUN_ID": "run_test",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_WF_EXEC_ID": "wf_test/exec_test",
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_ENVIRONMENT": "default",
        }
    )
    return env


def _run_script(
    script: str,
    gateway_url: str,
    *,
    python_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a Python script in a subprocess with the outbound gateway bootstrap on PYTHONPATH.

    Copies the bootstrap and sitecustomize modules into a temp directory,
    prepends it to PYTHONPATH, and executes the given script string so that
    all HTTP traffic is transparently routed through the mock gateway.
    """
    with TemporaryDirectory(prefix="tracecat_outbound_http_gateway_test_") as tmpdir:
        bootstrap_dir = Path(tmpdir)
        (bootstrap_dir / "outbound_http_gateway_bootstrap.py").write_bytes(
            Path(outbound_http_gateway_bootstrap_module.__file__).read_bytes()
        )
        (bootstrap_dir / "sitecustomize.py").write_bytes(
            Path(outbound_http_gateway_sitecustomize_module.__file__).read_bytes()
        )
        env = _bootstrap_env(gateway_url)
        if extra_env:
            env.update(extra_env)
        env["PYTHONPATH"] = (
            f"{bootstrap_dir}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
        )
        return subprocess.run(
            [sys.executable, *(python_args or []), "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )


_REQUESTS_SCRIPT = """\
import json, requests
response = requests.get(
    'https://example.com/test', params={'q': '1'}, headers={'X-Test': 'yes'}
)
print(json.dumps({'status': response.status_code, 'body': response.json()}))
"""

_HTTPX_SCRIPT = """\
import json, httpx
response = httpx.get(
    'https://example.com/test', params={'q': '1'}, headers={'X-Test': 'yes'}
)
print(json.dumps({'status': response.status_code, 'body': response.json()}))
"""

_URLLIB_SCRIPT = """\
import json, urllib.request
request = urllib.request.Request(
    'https://example.com/test?q=1', headers={'X-Test': 'yes'}
)
response = urllib.request.urlopen(request)
print(json.dumps({'status': response.status, 'body': json.loads(response.read().decode())}))
"""

_URLLIB3_SCRIPT = """\
import json, urllib3
http = urllib3.PoolManager()
response = http.request(
    'GET', 'https://example.com/test', fields={'q': '1'}, headers={'X-Test': 'yes'}
)
print(json.dumps({'status': response.status, 'body': json.loads(response.data.decode())}))
"""

_AIOHTTP_SCRIPT = """\
import asyncio, json, aiohttp

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.get(
            'https://example.com/test', params={'q': '1'}, headers={'X-Test': 'yes'}
        ) as response:
            payload = await response.json()
            print(json.dumps({'status': response.status, 'body': payload}))

asyncio.run(main())
"""

_REQUESTS_MULTIPART_SCRIPT = """\
import json, requests
response = requests.post(
    'https://example.com/upload',
    files={'file': ('hello.txt', b'hello world', 'text/plain')},
    data={'kind': 'sample'},
)
print(json.dumps({'status': response.status_code, 'body': response.json()}))
"""


@pytest.mark.parametrize(
    ("script", "expected_method"),
    [
        (_REQUESTS_SCRIPT, "GET"),
        (_HTTPX_SCRIPT, "GET"),
        (_URLLIB_SCRIPT, "GET"),
        (_URLLIB3_SCRIPT, "GET"),
        (_AIOHTTP_SCRIPT, "GET"),
    ],
)
def test_bootstrap_routes_common_clients_via_gateway(
    script: str,
    expected_method: str,
) -> None:
    """Verify that requests, httpx, and urllib traffic is routed through the gateway."""
    with _mock_gateway() as (gateway_url, state):
        completed = _run_script(script, gateway_url)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip())
    assert payload == {"status": 200, "body": {"ok": True}}
    [request] = state["requests"]
    assert request["path"] == "/v1/dev-proxy/dispatch"
    assert request["payload"]["method"] == expected_method
    assert request["payload"]["url"].startswith("https://example.com/test")
    assert request["headers"]["Authorization"] == "Bearer executor-token"
    assert request["headers"]["X-Tracecat-Action-Name"] == "core.http_request"


def test_bootstrap_errors_on_explicit_proxy_override() -> None:
    """Ensure the bootstrap rejects requests that specify explicit proxy settings."""
    script = (
        "import requests; "
        "requests.get('https://example.com/test', proxies={'https': 'http://proxy.local'})"
    )
    with _mock_gateway() as (gateway_url, _state):
        completed = _run_script(script, gateway_url)

    assert completed.returncode != 0
    assert "explicit proxy overrides are not supported" in completed.stderr.lower()


def test_bootstrap_routes_requests_multipart_via_gateway() -> None:
    """Buffered requests multipart uploads should be dispatched through the gateway."""
    with _mock_gateway() as (gateway_url, state):
        completed = _run_script(_REQUESTS_MULTIPART_SCRIPT, gateway_url)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip())
    assert payload == {"status": 200, "body": {"ok": True}}
    [request] = state["requests"]
    assert request["path"] == "/v1/dev-proxy/dispatch"
    assert request["payload"]["method"] == "POST"
    assert request["payload"]["url"] == "https://example.com/upload"
    content_type = request["payload"]["headers"]["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    decoded_body = base64.b64decode(request["payload"]["body_base64"]).decode("utf-8")
    assert 'name="kind"' in decoded_body
    assert 'name="file"; filename="hello.txt"' in decoded_body
    assert "hello world" in decoded_body


def test_bootstrap_bypasses_tracecat_api_host() -> None:
    """Requests to TRACECAT__API_URL should bypass the gateway entirely."""
    script = """\
import json, requests
response = requests.get('http://127.0.0.1:%s/internal/ping')
print(json.dumps({'status': response.status_code, 'body': response.json()}))
"""
    with (
        _mock_gateway() as (gateway_url, gateway_state),
        _mock_json_server() as (
            api_url,
            api_state,
        ),
    ):
        completed = _run_script(
            script % api_url.rsplit(":", maxsplit=1)[1],
            gateway_url,
            extra_env={"TRACECAT__API_URL": api_url},
        )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip())
    assert payload == {"status": 200, "body": {"direct": True}}
    assert gateway_state["requests"] == []
    assert len(api_state["requests"]) == 1
    assert api_state["requests"][0]["path"] == "/internal/ping"


def test_bootstrap_errors_when_targeting_gateway_origin() -> None:
    """Requests to the configured gateway origin should fail clearly."""
    script = """\
import requests
requests.get('http://127.0.0.1:%s/test-data')
"""
    with _mock_gateway() as (gateway_url, gateway_state):
        completed = _run_script(
            script % gateway_url.rsplit(":", maxsplit=1)[1],
            gateway_url,
        )

    assert completed.returncode != 0
    assert "configured outbound http gateway origin" in completed.stderr.lower()
    assert gateway_state["requests"] == []


def test_bootstrap_patches_modules_imported_before_hook_install() -> None:
    """Modules imported before hook installation should still be patched from sys.modules."""
    if _SITE_PACKAGES is None:
        pytest.skip("site-packages path is unavailable in this environment")
    script = f"""\
import json, sys
sys.path.append({_SITE_PACKAGES!r})
import requests, sitecustomize
sitecustomize._install_import_hooks()
response = requests.get('https://example.com/test', headers={{'X-Test': 'yes'}})
print(json.dumps({{
    'status': response.status_code,
    'body': response.json(),
    'patched': bool(getattr(requests.sessions.Session.request, '__tracecat_outbound_http_gateway__', False)),
}}))
"""
    with _mock_gateway() as (gateway_url, state):
        completed = _run_script(script, gateway_url, python_args=["-S"])

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip())
    assert payload == {"status": 200, "body": {"ok": True}, "patched": True}
    assert len(state["requests"]) == 1


@pytest.mark.anyio
async def test_httpx_async_patch_offloads_gateway_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_url = "https://example.com/test"
    original_sync_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def fake_dispatch(**_: Any) -> dict[str, Any]:
        time.sleep(0.1)
        return {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"ok":true}',
            "url": request_url,
            "reason_phrase": "OK",
        }

    monkeypatch.setattr(
        outbound_http_gateway_sitecustomize_module,
        "_dispatch_to_gateway",
        fake_dispatch,
    )
    outbound_http_gateway_sitecustomize_module._patch_httpx(httpx)
    try:
        async with httpx.AsyncClient() as client:
            response, ticks = await _count_event_loop_ticks(client.get(request_url))
    finally:
        httpx.Client.send = original_sync_send
        httpx.AsyncClient.send = original_async_send

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert ticks >= 3


@pytest.mark.anyio
async def test_aiohttp_async_patch_offloads_gateway_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_url = "https://example.com/test"
    original_request = aiohttp.ClientSession._request

    def fake_dispatch(**_: Any) -> dict[str, Any]:
        time.sleep(0.1)
        return {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"ok":true}',
            "url": request_url,
            "reason_phrase": "OK",
        }

    monkeypatch.setattr(
        outbound_http_gateway_sitecustomize_module,
        "_dispatch_to_gateway",
        fake_dispatch,
    )
    outbound_http_gateway_sitecustomize_module._patch_aiohttp(aiohttp)
    try:
        async with aiohttp.ClientSession() as session:

            async def make_request() -> tuple[int, dict[str, Any]]:
                async with session.get(request_url) as response:
                    return response.status, await response.json()

            result, ticks = await _count_event_loop_ticks(make_request())
    finally:
        aiohttp.ClientSession._request = original_request

    status_code, payload = result
    assert status_code == 200
    assert payload == {"ok": True}
    assert ticks >= 3


def test_httpx_sync_patch_uses_client_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    request_url = "https://example.com/test"
    original_sync_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send
    captured_timeout_ms: int | None = None

    def fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        nonlocal captured_timeout_ms
        captured_timeout_ms = kwargs["timeout_ms"]
        return {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"ok":true}',
            "url": request_url,
            "reason_phrase": "OK",
        }

    monkeypatch.setattr(
        outbound_http_gateway_sitecustomize_module,
        "_dispatch_to_gateway",
        fake_dispatch,
    )
    outbound_http_gateway_sitecustomize_module._patch_httpx(httpx)
    try:
        with httpx.Client(timeout=httpx.Timeout(2.0, connect=3.0)) as client:
            response = client.get(request_url)
    finally:
        httpx.Client.send = original_sync_send
        httpx.AsyncClient.send = original_async_send

    assert response.status_code == 200
    assert captured_timeout_ms == 3000


@pytest.mark.anyio
async def test_httpx_async_patch_prefers_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_url = "https://example.com/test"
    original_sync_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send
    captured_timeout_ms: int | None = None

    def fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        nonlocal captured_timeout_ms
        captured_timeout_ms = kwargs["timeout_ms"]
        return {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"ok":true}',
            "url": request_url,
            "reason_phrase": "OK",
        }

    monkeypatch.setattr(
        outbound_http_gateway_sitecustomize_module,
        "_dispatch_to_gateway",
        fake_dispatch,
    )
    outbound_http_gateway_sitecustomize_module._patch_httpx(httpx)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            request = client.build_request("GET", request_url, timeout=1.5)
            response = await client.send(request)
    finally:
        httpx.Client.send = original_sync_send
        httpx.AsyncClient.send = original_async_send

    assert response.status_code == 200
    assert captured_timeout_ms == 1500
