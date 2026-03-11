"""Simple outbound HTTP gateway for action interception QA.

Run locally with:

    uv run uvicorn tracecat.executor.outbound_http_gateway_server:app --host 0.0.0.0 --port 8787

The gateway accepts Tracecat's buffered dispatch envelope at
``POST /v1/dev-proxy/dispatch`` and either simulates a response or forwards the
request upstream.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
import orjson
from fastapi import FastAPI, Header, HTTPException, Request
from jwt import PyJWTError
from pydantic import BaseModel, Field, ValidationError

_EXECUTOR_TOKEN_ISSUER = "tracecat-executor"
_EXECUTOR_TOKEN_AUDIENCE = "tracecat-api"
_EXECUTOR_TOKEN_SUBJECT = "tracecat-executor"
_EXECUTOR_TOKEN_REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "iat",
    "exp",
    "workspace_id",
    "wf_id",
    "wf_exec_id",
)

_SIMULATE_HEADER = "x-tracecat-simulate"
_SIMULATE_STATUS_HEADER = "x-tracecat-simulate-status"
_SIMULATE_BODY_HEADER = "x-tracecat-simulate-body"
_SIMULATE_CONTENT_TYPE_HEADER = "x-tracecat-simulate-content-type"
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SIMULATION_CONTROL_HEADERS = frozenset(
    {
        _SIMULATE_HEADER,
        _SIMULATE_STATUS_HEADER,
        _SIMULATE_BODY_HEADER,
        _SIMULATE_CONTENT_TYPE_HEADER,
    }
)

logger = logging.getLogger(__name__)


class DispatchMode(StrEnum):
    SIMULATE = "simulate"
    FORWARD = "forward"


class DispatchRequest(BaseModel):
    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_base64: str = ""
    timeout_ms: int | None = None
    follow_redirects: bool = False


class DispatchResponse(BaseModel):
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body_base64: str
    url: str
    reason_phrase: str | None = None


class ExecutorTokenPayload(BaseModel):
    workspace_id: str
    user_id: str | None = None
    service_id: str | None = None
    wf_id: str
    wf_exec_id: str


@dataclass(frozen=True)
class OutboundHTTPGatewaySettings:
    listen_host: str = "0.0.0.0"
    listen_port: int = 8787
    verify_executor_token: bool = False
    executor_token_secret: str | None = None
    simulate_all: bool = False
    simulate_hosts: frozenset[str] = frozenset()
    default_simulated_status_code: int = 200
    default_simulated_content_type: str = "application/json"
    default_simulated_body: str | None = None
    default_forward_timeout_ms: int = 30000

    @classmethod
    def from_env(cls) -> OutboundHTTPGatewaySettings:
        return cls(
            listen_host=os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_LISTEN_HOST")
            or "0.0.0.0",
            listen_port=int(
                os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_LISTEN_PORT") or 8787
            ),
            verify_executor_token=_env_bool(
                "TRACECAT__OUTBOUND_HTTP_GATEWAY_VERIFY_EXECUTOR_TOKEN"
            ),
            executor_token_secret=os.environ.get("TRACECAT__SERVICE_KEY"),
            simulate_all=_env_bool("TRACECAT__OUTBOUND_HTTP_GATEWAY_SIMULATE_ALL"),
            simulate_hosts=frozenset(
                host.strip().lower()
                for host in (
                    os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_SIMULATE_HOSTS")
                    or ""
                ).split(",")
                if host.strip()
            ),
            default_simulated_status_code=int(
                os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_SIMULATE_STATUS_CODE")
                or 200
            ),
            default_simulated_content_type=(
                os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_SIMULATE_CONTENT_TYPE")
                or "application/json"
            ),
            default_simulated_body=os.environ.get(
                "TRACECAT__OUTBOUND_HTTP_GATEWAY_SIMULATE_BODY"
            ),
            default_forward_timeout_ms=int(
                os.environ.get("TRACECAT__OUTBOUND_HTTP_GATEWAY_FORWARD_TIMEOUT_MS")
                or 30000
            ),
        )


def _env_bool(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _decode_body(body_base64: str) -> bytes:
    try:
        return base64.b64decode(body_base64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="body_base64 is invalid") from exc


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(key): str(value) for key, value in headers.items()}


def _extract_target_host(url: str) -> str | None:
    try:
        if hostname := urlsplit(url).hostname:
            return hostname.lower()
        return None
    except Exception:
        return None


def _header_value(headers: dict[str, str], header_name: str) -> str | None:
    header_name_lower = header_name.lower()
    for key, value in headers.items():
        if key.lower() == header_name_lower:
            return value
    return None


def _sanitize_forward_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered in _SIMULATION_CONTROL_HEADERS:
            continue
        if lowered == "host":
            continue
        sanitized[key] = value
    return sanitized


def _sanitize_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }


def _read_metadata_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower().startswith("x-tracecat-")
    }


def _log_dispatch_decision(
    *,
    mode: DispatchMode,
    dispatch_request: DispatchRequest,
    request: Request,
) -> None:
    metadata_headers = _read_metadata_headers(request)
    logger.info(
        "Outbound HTTP gateway dispatch mode=%s method=%s url=%s action=%s ref=%s source=%s workflow_id=%s wf_exec_id=%s agent_session_id=%s",
        mode.value,
        dispatch_request.method,
        dispatch_request.url,
        metadata_headers.get("x-tracecat-action-name"),
        metadata_headers.get("x-tracecat-action-ref"),
        metadata_headers.get("x-tracecat-source"),
        metadata_headers.get("x-tracecat-workflow-id"),
        metadata_headers.get("x-tracecat-wf-exec-id"),
        metadata_headers.get("x-tracecat-agent-session-id"),
    )


def _parse_simulated_status(
    headers: dict[str, str], settings: OutboundHTTPGatewaySettings
) -> int:
    if raw_value := _header_value(headers, _SIMULATE_STATUS_HEADER):
        try:
            return int(raw_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{_SIMULATE_STATUS_HEADER} must be an integer",
            ) from exc
    return settings.default_simulated_status_code


def _select_mode(
    dispatch_request: DispatchRequest,
    settings: OutboundHTTPGatewaySettings,
) -> DispatchMode:
    if settings.simulate_all:
        return DispatchMode.SIMULATE
    if (
        _header_value(dispatch_request.headers, _SIMULATE_HEADER) or ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return DispatchMode.SIMULATE
    if _extract_target_host(dispatch_request.url) in settings.simulate_hosts:
        return DispatchMode.SIMULATE
    return DispatchMode.FORWARD


def _build_simulated_response(
    dispatch_request: DispatchRequest,
    request: Request,
    body: bytes,
    settings: OutboundHTTPGatewaySettings,
) -> DispatchResponse:
    content_type = _header_value(
        dispatch_request.headers, _SIMULATE_CONTENT_TYPE_HEADER
    ) or (settings.default_simulated_content_type)
    if response_body := _header_value(dispatch_request.headers, _SIMULATE_BODY_HEADER):
        encoded_body = response_body.encode("utf-8")
    elif settings.default_simulated_body is not None:
        encoded_body = settings.default_simulated_body.encode("utf-8")
    else:
        echo_payload: dict[str, Any] = {
            "simulated": True,
            "target": {
                "method": dispatch_request.method,
                "url": dispatch_request.url,
            },
            "request": {
                "headers": dispatch_request.headers,
            },
            "tracecat": _read_metadata_headers(request),
        }
        if body:
            try:
                echo_payload["request"]["body_text"] = body.decode("utf-8")
            except UnicodeDecodeError:
                echo_payload["request"]["body_base64"] = dispatch_request.body_base64
        encoded_body = orjson.dumps(echo_payload)

    return DispatchResponse(
        status_code=_parse_simulated_status(dispatch_request.headers, settings),
        headers={
            "Content-Type": content_type,
            "X-Tracecat-Gateway-Mode": DispatchMode.SIMULATE.value,
        },
        body_base64=base64.b64encode(encoded_body).decode("ascii"),
        url=dispatch_request.url,
        reason_phrase="Simulated",
    )


async def _forward_request(
    dispatch_request: DispatchRequest,
    body: bytes,
    settings: OutboundHTTPGatewaySettings,
) -> DispatchResponse:
    timeout_ms = dispatch_request.timeout_ms or settings.default_forward_timeout_ms
    async with httpx.AsyncClient(
        follow_redirects=dispatch_request.follow_redirects,
        timeout=timeout_ms / 1000,
    ) as client:
        response = await client.request(
            dispatch_request.method,
            dispatch_request.url,
            headers=_sanitize_forward_headers(dispatch_request.headers),
            content=body,
        )

    response_headers = _sanitize_response_headers(response.headers)
    response_headers["X-Tracecat-Gateway-Mode"] = DispatchMode.FORWARD.value
    return DispatchResponse(
        status_code=response.status_code,
        headers=response_headers,
        body_base64=base64.b64encode(response.content).decode("ascii"),
        url=str(response.url),
        reason_phrase=response.reason_phrase,
    )


def _verify_incoming_executor_token(
    authorization: str | None,
    settings: OutboundHTTPGatewaySettings,
) -> ExecutorTokenPayload:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if not settings.executor_token_secret:
        raise HTTPException(
            status_code=500,
            detail="Executor token verification is enabled but no signing key is configured",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            settings.executor_token_secret,
            algorithms=["HS256"],
            audience=_EXECUTOR_TOKEN_AUDIENCE,
            issuer=_EXECUTOR_TOKEN_ISSUER,
            options={"require": list(_EXECUTOR_TOKEN_REQUIRED_CLAIMS)},
        )
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid executor token") from exc
    if payload.get("sub") != _EXECUTOR_TOKEN_SUBJECT:
        raise HTTPException(status_code=401, detail="Invalid executor token")
    try:
        return ExecutorTokenPayload.model_validate(
            {
                "workspace_id": payload["workspace_id"],
                "user_id": payload.get("user_id"),
                "service_id": payload.get("service_id"),
                "wf_id": payload["wf_id"],
                "wf_exec_id": payload["wf_exec_id"],
            }
        )
    except (KeyError, ValidationError) as exc:
        raise HTTPException(status_code=401, detail="Invalid executor token") from exc


def create_outbound_http_gateway_app(
    settings: OutboundHTTPGatewaySettings | None = None,
) -> FastAPI:
    gateway_settings = settings or OutboundHTTPGatewaySettings.from_env()
    app = FastAPI(title="Tracecat outbound HTTP gateway", version="0.1.0")

    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "simulate_all": gateway_settings.simulate_all,
            "simulate_hosts": sorted(gateway_settings.simulate_hosts),
            "verify_executor_token": gateway_settings.verify_executor_token,
        }

    async def dispatch(
        dispatch_request: DispatchRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> DispatchResponse:
        if gateway_settings.verify_executor_token:
            _verify_incoming_executor_token(authorization, gateway_settings)

        dispatch_request.headers = _normalize_headers(dispatch_request.headers)
        body = _decode_body(dispatch_request.body_base64)
        mode = _select_mode(dispatch_request, gateway_settings)
        _log_dispatch_decision(
            mode=mode,
            dispatch_request=dispatch_request,
            request=request,
        )
        if mode is DispatchMode.SIMULATE:
            return _build_simulated_response(
                dispatch_request, request, body, gateway_settings
            )
        return await _forward_request(dispatch_request, body, gateway_settings)

    app.get("/healthz")(healthz)
    app.post("/v1/dev-proxy/dispatch", response_model=DispatchResponse)(dispatch)

    return app


app = create_outbound_http_gateway_app()


if __name__ == "__main__":
    import uvicorn

    settings = OutboundHTTPGatewaySettings.from_env()
    uvicorn.run(
        create_outbound_http_gateway_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
    )
