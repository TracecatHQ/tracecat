"""Interpreter startup hook for Tracecat outbound HTTP gateway interception.

This file is copied into a per-run bootstrap directory as ``sitecustomize.py``
so it is imported automatically before user code in direct and ephemeral
executor subprocesses.

It is intentionally self-contained and must not import ``tracecat``.
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportFunctionMemberAccess=false, reportIncompatibleMethodOverride=false, reportOptionalMemberAccess=false

from __future__ import annotations

import asyncio
import base64
import contextvars
import email.message
import http.client
import importlib.abc
import importlib.machinery
import io
import json
import os
import socket
import ssl
import sys
import types
from collections.abc import Iterable, Mapping
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.response import addinfourl

if TYPE_CHECKING:
    from typing import NotRequired, TypedDict

    class _GatewayDispatchRequest(TypedDict):
        method: str
        url: str
        headers: dict[str, str]
        body_base64: str
        timeout_ms: int | None
        follow_redirects: bool

    class _GatewayDispatchResponseWire(TypedDict):
        status_code: int
        headers: NotRequired[dict[str, str]]
        body_base64: str
        url: NotRequired[str]
        reason_phrase: NotRequired[str]

    class _GatewayDispatchResponse(TypedDict):
        status_code: int
        headers: dict[str, str]
        body: bytes
        url: str
        reason_phrase: str | None
else:
    _GatewayDispatchRequest = dict[str, object]
    _GatewayDispatchResponseWire = dict[str, object]
    _GatewayDispatchResponse = dict[str, object]

_OUTBOUND_HTTP_GATEWAY_ENABLED = (
    os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_ENABLED") == "1"
)
_GATEWAY_URL = os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_URL", "")
_API_URL = os.environ.get("TRACECAT__API_URL", "")
_IN_FLIGHT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tracecat_outbound_http_gateway_in_flight",
    default=False,
)

_METADATA_HEADERS = {
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_WORKSPACE_ID": "X-Tracecat-Workspace-Id",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_ORGANIZATION_ID": "X-Tracecat-Organization-Id",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_ENVIRONMENT": "X-Tracecat-Environment",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_SOURCE": "X-Tracecat-Source",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_ACTION_NAME": "X-Tracecat-Action-Name",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_ACTION_REF": "X-Tracecat-Action-Ref",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_BACKEND": "X-Tracecat-Backend",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_WORKFLOW_ID": "X-Tracecat-Workflow-Id",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_WF_EXEC_ID": "X-Tracecat-Wf-Exec-Id",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_RUN_ID": "X-Tracecat-Run-Id",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_EXECUTION_TYPE": "X-Tracecat-Execution-Type",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_TRIGGER_TYPE": "X-Tracecat-Trigger-Type",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_AGENT_SESSION_ID": "X-Tracecat-Agent-Session-Id",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_ENTITY_TYPE": "X-Tracecat-Entity-Type",
    "TRACECAT__OUTBOUND_HTTP_GATEWAY_ENTITY_ID": "X-Tracecat-Entity-Id",
}
_DISPATCH_PATH = "/v1/dev-proxy/dispatch"


class TracecatOutboundHTTPGatewayError(RuntimeError):
    """Raised when outbound gateway interception cannot safely handle a request."""


def _url_host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except Exception:
        return None


def _url_origin(url: str) -> str | None:
    try:
        split = urlsplit(url)
    except Exception:
        return None
    if not split.scheme or not split.netloc:
        return None
    return f"{split.scheme}://{split.netloc}"


_GATEWAY_HOST = _url_host(_GATEWAY_URL)
_API_HOST = _url_host(_API_URL)
_GATEWAY_ORIGIN = _url_origin(_GATEWAY_URL)
_API_ORIGIN = _url_origin(_API_URL)


def _ensure_gateway_enabled() -> None:
    if _OUTBOUND_HTTP_GATEWAY_ENABLED and not _GATEWAY_URL:
        raise TracecatOutboundHTTPGatewayError(
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_URL is required when outbound HTTP interception is enabled"
        )


def _build_metadata_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    for env_key, header_name in _METADATA_HEADERS.items():
        if value := os.environ.get(env_key):
            headers[header_name] = value
    return headers


def _bool_or_default(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)


def _resolve_follow_redirects(value: object, default: bool) -> bool:
    return _bool_or_default(value, default)


def _resolve_timeout_ms(value: object) -> int | None:
    if value is None:
        return None
    if value is socket._GLOBAL_DEFAULT_TIMEOUT:
        return None
    if isinstance(value, (int, float)):
        return max(int(float(value) * 1000), 0)
    if isinstance(value, tuple | list):
        numeric_values = [float(item) for item in value if item is not None]
        if numeric_values:
            return max(int(max(numeric_values) * 1000), 0)
        return None
    total = getattr(value, "total", None)
    if isinstance(total, (int, float)):
        return max(int(float(total) * 1000), 0)
    connect = getattr(value, "connect", None)
    read = getattr(value, "read", None)
    if total is None and connect is None and read is None:
        return None
    if isinstance(connect, (int, float)) or isinstance(read, (int, float)):
        numeric_values = [
            float(item) for item in (connect, read) if isinstance(item, (int, float))
        ]
        if numeric_values:
            return max(int(max(numeric_values) * 1000), 0)
    raise TracecatOutboundHTTPGatewayError(f"Unsupported timeout value: {value!r}")


def _resolve_httpx_timeout_ms(request: object, client: object) -> int | None:
    request_extensions = getattr(request, "extensions", {})
    request_timeout = (
        request_extensions.get("timeout")
        if isinstance(request_extensions, Mapping)
        else None
    )
    if isinstance(request_timeout, Mapping):
        numeric_values = [
            float(item)
            for item in request_timeout.values()
            if isinstance(item, (int, float))
        ]
        if numeric_values:
            return max(int(max(numeric_values) * 1000), 0)
        return None
    return _resolve_timeout_ms(request_timeout or getattr(client, "timeout", None))


def _encode_body(
    body: object,
    *,
    json_value: object | None = None,
    content_type: str | None = None,
) -> tuple[bytes, str | None]:
    if json_value is not None:
        payload = json.dumps(json_value).encode("utf-8")
        return payload, content_type or "application/json"
    if body is None:
        return b"", content_type
    if isinstance(body, (bytes, bytearray, memoryview)):
        return bytes(body), content_type
    if isinstance(body, str):
        return body.encode("utf-8"), content_type
    if isinstance(body, Mapping):
        payload = urlencode(list(body.items()), doseq=True).encode("utf-8")
        return payload, content_type or "application/x-www-form-urlencoded"
    if isinstance(body, list | tuple):
        payload = urlencode(body, doseq=True).encode("utf-8")
        return payload, content_type or "application/x-www-form-urlencoded"
    if hasattr(body, "read"):
        raise TracecatOutboundHTTPGatewayError(
            "Streaming request bodies are not supported"
        )
    if isinstance(body, Iterable):
        raise TracecatOutboundHTTPGatewayError(
            "Iterable request bodies are not supported"
        )
    raise TracecatOutboundHTTPGatewayError(
        f"Unsupported request body type: {type(body).__name__}"
    )


def _merge_query(url: str, params: object | None) -> str:
    if params is None:
        return url
    split = urlsplit(url)
    existing = parse_qsl(split.query, keep_blank_values=True)
    if isinstance(params, Mapping):
        new_pairs = list(params.items())
    elif isinstance(params, list | tuple):
        new_pairs = list(params)
    else:
        raise TracecatOutboundHTTPGatewayError(
            f"Unsupported query params type: {type(params).__name__}"
        )
    query = urlencode(existing + new_pairs, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


def _should_bypass(url: str) -> bool:
    if _IN_FLIGHT.get():
        return True
    origin = _url_origin(url)
    return origin is not None and origin == _API_ORIGIN


def _ensure_not_gateway_target(url: str) -> None:
    if _GATEWAY_ORIGIN is None:
        return
    if _url_origin(url) == _GATEWAY_ORIGIN:
        raise TracecatOutboundHTTPGatewayError(
            "Requests targeting the configured outbound HTTP gateway origin are not allowed"
        )


def _dispatch_to_gateway(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_ms: int | None,
    follow_redirects: bool,
) -> _GatewayDispatchResponse:
    _ensure_not_gateway_target(url)
    gateway_url = _GATEWAY_URL.rstrip("/")
    gateway = urlsplit(f"{gateway_url}{_DISPATCH_PATH}")
    if gateway.scheme not in {"http", "https"}:
        raise TracecatOutboundHTTPGatewayError(
            f"Unsupported gateway scheme for {_GATEWAY_URL!r}: {gateway.scheme!r}"
        )
    path = gateway.path or "/"
    if gateway.query:
        path = f"{path}?{gateway.query}"
    request_payload: _GatewayDispatchRequest = {
        "method": method,
        "url": url,
        "headers": dict(headers),
        "body_base64": base64.b64encode(body).decode("ascii"),
        "timeout_ms": timeout_ms,
        "follow_redirects": follow_redirects,
    }
    payload = json.dumps(request_payload).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(payload)),
        **_build_metadata_headers(),
    }
    if token := os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_AUTH_TOKEN"):
        request_headers["Authorization"] = f"Bearer {token}"

    token = _IN_FLIGHT.set(True)
    try:
        if gateway.scheme == "https":
            connection = http.client.HTTPSConnection(
                gateway.hostname,
                gateway.port or 443,
                timeout=(timeout_ms / 1000) if timeout_ms is not None else None,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                gateway.hostname,
                gateway.port or 80,
                timeout=(timeout_ms / 1000) if timeout_ms is not None else None,
            )
        try:
            connection.request("POST", path, body=payload, headers=request_headers)
            response = connection.getresponse()
            raw_body = response.read()
        finally:
            connection.close()
    finally:
        _IN_FLIGHT.reset(token)

    if response.status >= 400:
        detail = raw_body.decode("utf-8", errors="replace")[:200]
        raise TracecatOutboundHTTPGatewayError(
            f"Gateway dispatch failed with {response.status}: {detail}"
        )

    try:
        envelope: _GatewayDispatchResponseWire = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise TracecatOutboundHTTPGatewayError("Gateway returned invalid JSON") from exc

    body_base64 = envelope.get("body_base64", "")
    if not isinstance(body_base64, str):
        raise TracecatOutboundHTTPGatewayError(
            "Gateway response body_base64 must be a string"
        )
    try:
        decoded_body = base64.b64decode(body_base64)
    except Exception as exc:  # noqa: BLE001
        raise TracecatOutboundHTTPGatewayError(
            "Gateway response body_base64 is invalid"
        ) from exc
    raw_headers = envelope.get("headers") or {}
    response_headers = {str(key): str(value) for key, value in raw_headers.items()}
    return {
        "status_code": int(envelope["status_code"]),
        "headers": response_headers,
        "body": decoded_body,
        "url": str(envelope.get("url") or url),
        "reason_phrase": (
            str(reason_phrase)
            if (reason_phrase := envelope.get("reason_phrase")) is not None
            else None
        ),
    }


async def _dispatch_to_gateway_async(**kwargs: object) -> _GatewayDispatchResponse:
    return await asyncio.to_thread(_dispatch_to_gateway, **kwargs)


def _build_requests_response(
    requests_module: types.ModuleType,
    prepared_request: object,
    envelope: _GatewayDispatchResponse,
) -> object:
    response = requests_module.Response()
    response.status_code = int(envelope["status_code"])
    response._content = envelope["body"]
    response.headers = requests_module.structures.CaseInsensitiveDict(
        envelope.get("headers") or {}
    )
    response.url = str(envelope.get("url") or prepared_request.url)
    response.reason = envelope.get("reason_phrase")
    response.request = prepared_request
    response.encoding = None
    response.raw = types.SimpleNamespace(
        _original_response=types.SimpleNamespace(msg=_to_http_message(envelope))
    )
    response.history = []
    return response


def _to_http_message(envelope: _GatewayDispatchResponse) -> email.message.Message:
    message = email.message.Message()
    for key, value in (envelope.get("headers") or {}).items():
        message[key] = value
    return message


def _finalize_requests_response(
    requests_module: types.ModuleType,
    session: object,
    prepared_request: object,
    response: object,
    *,
    stream: bool | None,
    timeout: object | None,
    verify: object | None,
    cert: object | None,
    proxies: object | None,
) -> object:
    response = requests_module.hooks.dispatch_hook(
        "response",
        prepared_request.hooks,
        response,
        stream=stream,
        timeout=timeout,
        verify=verify,
        cert=cert,
        proxies=proxies,
    )
    if hasattr(session, "cookies") and getattr(response, "raw", None) is not None:
        requests_module.cookies.extract_cookies_to_jar(
            session.cookies, prepared_request, response.raw
        )
    if not _bool_or_default(stream, False):
        _ = response.content
    return response


def _build_httpx_response(
    httpx_module: types.ModuleType,
    request: object,
    envelope: _GatewayDispatchResponse,
) -> object:
    response = httpx_module.Response(
        status_code=int(envelope["status_code"]),
        headers=envelope.get("headers") or {},
        content=envelope["body"],
        request=request,
    )
    if reason_phrase := envelope.get("reason_phrase"):
        response.extensions["reason_phrase"] = reason_phrase
    return response


def _prepare_httpx_auth(
    client: object,
    request: object,
    auth: object,
    sentinel: object,
) -> tuple[object, object | None]:
    auth_obj = client._build_request_auth(
        request, auth if auth is not sentinel else sentinel
    )
    auth_flow = auth_obj.sync_auth_flow(request)
    try:
        prepared_request = next(auth_flow)
    except StopIteration:
        auth_flow.close()
        return request, None
    return prepared_request, auth_flow


def _finalize_httpx_auth(response: object, auth_flow: object | None) -> None:
    if auth_flow is None:
        return
    try:
        try:
            auth_flow.send(response)
        except StopIteration:
            return
        raise TracecatOutboundHTTPGatewayError(
            "Challenge-based httpx auth is not supported with outbound HTTP interception"
        )
    finally:
        auth_flow.close()


async def _prepare_httpx_auth_async(
    client: object,
    request: object,
    auth: object,
    sentinel: object,
) -> tuple[object, object | None]:
    auth_obj = client._build_request_auth(
        request, auth if auth is not sentinel else sentinel
    )
    auth_flow = auth_obj.async_auth_flow(request)
    try:
        prepared_request = await auth_flow.__anext__()
    except StopAsyncIteration:
        await auth_flow.aclose()
        return request, None
    return prepared_request, auth_flow


async def _finalize_httpx_auth_async(
    response: object, auth_flow: object | None
) -> None:
    if auth_flow is None:
        return
    try:
        try:
            await auth_flow.asend(response)
        except StopAsyncIteration:
            return
        raise TracecatOutboundHTTPGatewayError(
            "Challenge-based httpx auth is not supported with outbound HTTP interception"
        )
    finally:
        await auth_flow.aclose()


def _apply_aiohttp_auth(headers: dict[str, str], auth: object | None) -> None:
    if auth is None:
        return
    if "Authorization" in headers or "authorization" in headers:
        return
    if hasattr(auth, "encode"):
        headers["Authorization"] = auth.encode()
        return
    raise TracecatOutboundHTTPGatewayError(
        "Unsupported aiohttp auth configuration with outbound HTTP interception"
    )


def _apply_aiohttp_cookies(
    aiohttp_module: types.ModuleType,
    session: object,
    headers: dict[str, str],
    url: str,
    cookies: object | None,
) -> None:
    if "Cookie" in headers or "cookie" in headers:
        return
    url_obj = aiohttp_module.client_reqrep.URL(url)
    merged = SimpleCookie()
    if (cookie_jar := getattr(session, "_cookie_jar", None)) is not None:
        session_cookies = cookie_jar.filter_cookies(url_obj)
        if session_cookies:
            merged.load(session_cookies)
    if cookies is not None and (
        (cookie_jar := getattr(session, "_cookie_jar", None)) is not None
    ):
        temp_jar = aiohttp_module.CookieJar(quote_cookie=cookie_jar.quote_cookie)
        temp_jar.update_cookies(cookies)
        request_cookies = temp_jar.filter_cookies(url_obj)
        if request_cookies:
            merged.load(request_cookies)
    if merged:
        headers["Cookie"] = merged.output(header="", sep=";").strip()


def _update_aiohttp_cookie_jar(
    aiohttp_module: types.ModuleType,
    session: object,
    response: object,
) -> None:
    if (cookie_jar := getattr(session, "_cookie_jar", None)) is None:
        return
    if set_cookie := response.headers.get("Set-Cookie"):
        cookie_jar.update_cookies(
            SimpleCookie(set_cookie),
            response_url=aiohttp_module.client_reqrep.URL(response.url),
        )


def _build_urllib3_response(
    urllib3_module: types.ModuleType,
    envelope: _GatewayDispatchResponse,
) -> object:
    return urllib3_module.response.HTTPResponse(
        body=io.BytesIO(envelope["body"]),
        headers=envelope.get("headers") or {},
        status=int(envelope["status_code"]),
        reason=envelope.get("reason_phrase"),
        preload_content=False,
    )


def _build_urllib_response(
    url: str,
    envelope: _GatewayDispatchResponse,
) -> addinfourl:
    headers = email.message.Message()
    for key, value in (envelope.get("headers") or {}).items():
        headers[key] = value
    response = addinfourl(
        io.BytesIO(envelope["body"]),
        headers,
        str(envelope.get("url") or url),
        int(envelope["status_code"]),
    )
    response.msg = envelope.get("reason_phrase")
    return response


def _raise_urllib_http_error(
    urllib_request_module: types.ModuleType,
    url: str,
    envelope: _GatewayDispatchResponse,
) -> None:
    headers = email.message.Message()
    for key, value in (envelope.get("headers") or {}).items():
        headers[key] = value
    raise urllib_request_module.HTTPError(
        str(envelope.get("url") or url),
        int(envelope["status_code"]),
        envelope.get("reason_phrase") or "",
        headers,
        io.BytesIO(envelope["body"]),
    )


class _AiohttpProxyResponse:
    def __init__(
        self,
        aiohttp_module: types.ModuleType,
        envelope: _GatewayDispatchResponse,
    ):
        self._aiohttp = aiohttp_module
        self.status = int(envelope["status_code"])
        self.reason = envelope.get("reason_phrase")
        self.headers = dict(envelope.get("headers") or {})
        self.url = envelope.get("url")
        self.real_url = self.url
        self.ok = 200 <= self.status < 400
        self._body = bytes(envelope["body"])
        self.closed = False

    async def __aenter__(self) -> _AiohttpProxyResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.close()

    async def read(self) -> bytes:
        return self._body

    async def text(self, encoding: str | None = None, errors: str = "strict") -> str:
        return self._body.decode(encoding or "utf-8", errors=errors)

    async def json(self, encoding: str | None = None) -> object:
        return json.loads((await self.text(encoding=encoding)).strip() or "null")

    def release(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise self._aiohttp.ClientResponseError(
                request_info=None,
                history=(),
                status=self.status,
                message=self.reason or "",
                headers=self.headers,
            )


def _patch_requests(requests_module: types.ModuleType) -> None:
    session_cls = requests_module.sessions.Session
    if getattr(session_cls.request, "__tracecat_outbound_http_gateway__", False):
        return
    original = session_cls.request

    def patched(
        self: object,
        method: str,
        url: str,
        params: object | None = None,
        data: object | None = None,
        headers: Mapping[str, str] | None = None,
        cookies: object | None = None,
        files: object | None = None,
        auth: object | None = None,
        timeout: object | None = None,
        allow_redirects: bool = True,
        proxies: object | None = None,
        hooks: object | None = None,
        stream: bool | None = None,
        verify: object | None = None,
        cert: object | None = None,
        json: object | None = None,
    ) -> object:
        if proxies:
            raise TracecatOutboundHTTPGatewayError(
                "Explicit proxy overrides are not supported with outbound HTTP interception"
            )
        if _bool_or_default(stream, False):
            raise TracecatOutboundHTTPGatewayError(
                "Streaming responses are not supported with outbound HTTP interception"
            )
        request = requests_module.Request(
            method=method.upper(),
            url=url,
            headers=headers,
            files=files,
            data=data,
            json=json,
            params=params,
            auth=auth,
            cookies=cookies,
            hooks=hooks,
        )
        prepared = self.prepare_request(request)
        if _should_bypass(prepared.url):
            return original(
                self,
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                cookies=cookies,
                files=files,
                auth=auth,
                timeout=timeout,
                allow_redirects=allow_redirects,
                proxies=proxies,
                hooks=hooks,
                stream=stream,
                verify=verify,
                cert=cert,
                json=json,
            )
        effective_verify = (
            verify if verify is not None else getattr(self, "verify", True)
        )
        effective_cert = cert if cert is not None else getattr(self, "cert", None)
        if effective_verify is not True or effective_cert is not None:
            raise TracecatOutboundHTTPGatewayError(
                "TLS verify/cert overrides are not supported with outbound HTTP interception"
            )
        body, content_type = _encode_body(
            prepared.body,
            content_type=prepared.headers.get("Content-Type"),
        )
        if content_type and "Content-Type" not in prepared.headers:
            prepared.headers["Content-Type"] = content_type
        envelope = _dispatch_to_gateway(
            method=prepared.method,
            url=prepared.url,
            headers=prepared.headers,
            body=body,
            timeout_ms=_resolve_timeout_ms(timeout),
            follow_redirects=_resolve_follow_redirects(allow_redirects, True),
        )
        response = _build_requests_response(requests_module, prepared, envelope)
        return _finalize_requests_response(
            requests_module,
            self,
            prepared,
            response,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )

    patched.__tracecat_outbound_http_gateway__ = True
    session_cls.request = patched


def _patch_httpx(httpx_module: types.ModuleType) -> None:
    if getattr(httpx_module.Client.send, "__tracecat_outbound_http_gateway__", False):
        return
    original_send = httpx_module.Client.send
    original_async_send = httpx_module.AsyncClient.send
    sentinel = httpx_module.USE_CLIENT_DEFAULT

    def sync_patched(
        self: object,
        request: object,
        *,
        stream: bool = False,
        auth: object = sentinel,
        follow_redirects: object = sentinel,
    ) -> object:
        request_url = str(request.url)
        if _should_bypass(request_url):
            return original_send(
                self,
                request,
                stream=stream,
                auth=auth,
                follow_redirects=follow_redirects,
            )
        if stream:
            raise TracecatOutboundHTTPGatewayError(
                "Streaming responses are not supported with outbound HTTP interception"
            )
        request, auth_flow = _prepare_httpx_auth(self, request, auth, sentinel)
        request_url = str(request.url)
        body = request.content
        envelope = _dispatch_to_gateway(
            method=request.method,
            url=request_url,
            headers=request.headers,
            body=body,
            timeout_ms=_resolve_httpx_timeout_ms(request, self),
            follow_redirects=_resolve_follow_redirects(
                self.follow_redirects
                if follow_redirects is sentinel
                else follow_redirects,
                bool(self.follow_redirects),
            ),
        )
        response = _build_httpx_response(httpx_module, request, envelope)
        _finalize_httpx_auth(response, auth_flow)
        return response

    async def async_patched(
        self: object,
        request: object,
        *,
        stream: bool = False,
        auth: object = sentinel,
        follow_redirects: object = sentinel,
    ) -> object:
        request_url = str(request.url)
        if _should_bypass(request_url):
            return await original_async_send(
                self,
                request,
                stream=stream,
                auth=auth,
                follow_redirects=follow_redirects,
            )
        if stream:
            raise TracecatOutboundHTTPGatewayError(
                "Streaming responses are not supported with outbound HTTP interception"
            )
        request, auth_flow = await _prepare_httpx_auth_async(
            self, request, auth, sentinel
        )
        request_url = str(request.url)
        body = await request.aread()
        envelope = await _dispatch_to_gateway_async(
            method=request.method,
            url=request_url,
            headers=request.headers,
            body=body,
            timeout_ms=_resolve_httpx_timeout_ms(request, self),
            follow_redirects=_resolve_follow_redirects(
                self.follow_redirects
                if follow_redirects is sentinel
                else follow_redirects,
                bool(self.follow_redirects),
            ),
        )
        response = _build_httpx_response(httpx_module, request, envelope)
        await _finalize_httpx_auth_async(response, auth_flow)
        return response

    sync_patched.__tracecat_outbound_http_gateway__ = True
    async_patched.__tracecat_outbound_http_gateway__ = True
    httpx_module.Client.send = sync_patched
    httpx_module.AsyncClient.send = async_patched


def _patch_urllib3(urllib3_module: types.ModuleType) -> None:
    pool_cls = urllib3_module.PoolManager
    if getattr(pool_cls.urlopen, "__tracecat_outbound_http_gateway__", False):
        return
    original = pool_cls.urlopen

    def patched(
        self: object,
        method: str,
        url: str,
        body: object | None = None,
        headers: Mapping[str, str] | None = None,
        retries: object | None = None,
        redirect: bool = True,
        assert_same_host: bool = True,
        timeout: object | None = None,
        pool_timeout: object | None = None,
        release_conn: bool | None = None,
        chunked: bool = False,
        body_pos: object | None = None,
        preload_content: bool = True,
        decode_content: bool = True,
        **response_kw: object,
    ) -> object:
        if _should_bypass(url):
            return original(
                self,
                method,
                url,
                body=body,
                headers=headers,
                retries=retries,
                redirect=redirect,
                assert_same_host=assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                preload_content=preload_content,
                decode_content=decode_content,
                **response_kw,
            )
        if chunked or not preload_content:
            raise TracecatOutboundHTTPGatewayError(
                "Streaming HTTP bodies are not supported with outbound HTTP interception"
            )
        encoded_body, content_type = _encode_body(
            body,
            content_type=(headers or {}).get("Content-Type"),
        )
        request_headers = dict(headers or {})
        if content_type and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = content_type
        envelope = _dispatch_to_gateway(
            method=method.upper(),
            url=url,
            headers=request_headers,
            body=encoded_body,
            timeout_ms=_resolve_timeout_ms(timeout),
            follow_redirects=_resolve_follow_redirects(redirect, True),
        )
        return _build_urllib3_response(urllib3_module, envelope)

    patched.__tracecat_outbound_http_gateway__ = True
    pool_cls.urlopen = patched


def _patch_urllib_request(urllib_request_module: types.ModuleType) -> None:
    opener_cls = urllib_request_module.OpenerDirector
    if getattr(opener_cls.open, "__tracecat_outbound_http_gateway__", False):
        return
    original = opener_cls.open

    def patched(
        self: object,
        fullurl: object,
        data: object | None = None,
        timeout: object | None = None,
    ) -> object:
        if isinstance(fullurl, urllib_request_module.Request):
            url = fullurl.full_url
            headers = dict(fullurl.header_items())
            body = fullurl.data if data is None else data
            method = getattr(fullurl, "method", None) or (
                "POST" if body is not None else "GET"
            )
        else:
            url = str(fullurl)
            method = "POST" if data is not None else "GET"
            headers = {}
            body = data
        if urlsplit(url).scheme.lower() not in {"http", "https"}:
            return original(self, fullurl, data=data, timeout=timeout)
        if _should_bypass(url):
            return original(self, fullurl, data=data, timeout=timeout)
        encoded_body, content_type = _encode_body(
            body,
            content_type=headers.get("Content-Type"),
        )
        if content_type and "Content-Type" not in headers:
            headers["Content-Type"] = content_type
        envelope = _dispatch_to_gateway(
            method=method,
            url=url,
            headers=headers,
            body=encoded_body,
            timeout_ms=_resolve_timeout_ms(timeout),
            follow_redirects=True,
        )
        if int(envelope["status_code"]) >= 400:
            _raise_urllib_http_error(urllib_request_module, url, envelope)
        return _build_urllib_response(url, envelope)

    patched.__tracecat_outbound_http_gateway__ = True
    opener_cls.open = patched


def _patch_aiohttp(aiohttp_module: types.ModuleType) -> None:
    session_cls = aiohttp_module.ClientSession
    if getattr(session_cls._request, "__tracecat_outbound_http_gateway__", False):
        return
    original = session_cls._request

    async def patched(
        self: object,
        method: str,
        str_or_url: object,
        **kwargs: object,
    ) -> object:
        url = _merge_query(str(str_or_url), kwargs.get("params"))
        if _should_bypass(url):
            return await original(self, method, str_or_url, **kwargs)
        if kwargs.get("proxy") is not None or kwargs.get("proxy_auth") is not None:
            raise TracecatOutboundHTTPGatewayError(
                "Explicit proxy overrides are not supported with outbound HTTP interception"
            )
        if _bool_or_default(kwargs.get("chunked"), False):
            raise TracecatOutboundHTTPGatewayError(
                "Streaming request bodies are not supported with outbound HTTP interception"
            )
        data = kwargs.get("data")
        if data is not None and hasattr(data, "__aiter__"):
            raise TracecatOutboundHTTPGatewayError(
                "Async streaming request bodies are not supported with outbound HTTP interception"
            )
        headers = dict(getattr(self, "_default_headers", {}) or {})
        if isinstance(kwargs.get("headers"), Mapping):
            headers.update(kwargs["headers"])
        auth = (
            kwargs["auth"] if "auth" in kwargs else getattr(self, "_default_auth", None)
        )
        _apply_aiohttp_auth(headers, auth)
        _apply_aiohttp_cookies(
            aiohttp_module, self, headers, url, kwargs.get("cookies")
        )
        encoded_body, content_type = _encode_body(
            data,
            json_value=kwargs.get("json"),
            content_type=headers.get("Content-Type"),
        )
        if content_type and "Content-Type" not in headers:
            headers["Content-Type"] = content_type
        envelope = await _dispatch_to_gateway_async(
            method=method.upper(),
            url=url,
            headers=headers,
            body=encoded_body,
            timeout_ms=_resolve_timeout_ms(
                kwargs.get("timeout", getattr(self, "_timeout", None))
            ),
            follow_redirects=_resolve_follow_redirects(
                kwargs.get("allow_redirects"), True
            ),
        )
        response = _AiohttpProxyResponse(aiohttp_module, envelope)
        _update_aiohttp_cookie_jar(aiohttp_module, self, response)
        return response

    patched.__tracecat_outbound_http_gateway__ = True
    session_cls._request = patched


_PATCHERS = {
    "requests": _patch_requests,
    "httpx": _patch_httpx,
    "urllib3": _patch_urllib3,
    "urllib.request": _patch_urllib_request,
    "aiohttp": _patch_aiohttp,
}


class _LoaderWrapper(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader, fullname: str) -> None:
        self._loader = loader
        self._fullname = fullname

    def create_module(self, spec: object) -> object:
        if hasattr(self._loader, "create_module"):
            return self._loader.create_module(spec)  # type: ignore[misc]
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        self._loader.exec_module(module)
        _PATCHERS[self._fullname](module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object | None = None,
        target: object | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname not in _PATCHERS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _LoaderWrapper(spec.loader, fullname)
        return spec


def _install_import_hooks() -> None:
    for name, patcher in _PATCHERS.items():
        if module := sys.modules.get(name):
            patcher(module)
    sys.meta_path.insert(0, _PatchFinder())


if _OUTBOUND_HTTP_GATEWAY_ENABLED:
    _ensure_gateway_enabled()
    _install_import_hooks()
