"""Cheap, cached connectivity checks for a chat's selected model."""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import orjson
import sqlalchemy as sa
from cachetools import TTLCache
from sqlalchemy import select

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.diagnostics import (
    MAX_LLM_ERROR_BODY_BYTES,
    parse_bounded_error_body,
)
from tracecat.agent.gateway_providers import (
    GATEWAY_PROVIDER_SPECS,
    is_gateway_provider,
    resolve_gateway_provider_config,
    strip_openai_version_suffix,
)
from tracecat.agent.model_status.errors import model_error_detail
from tracecat.agent.model_status.schemas import ModelErrorDetail, ModelStatusRead
from tracecat.agent.model_status.types import ModelErrorCode, ModelStatusState
from tracecat.agent.service import AgentManagementService
from tracecat.db.models import AgentCatalog
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.network import DisallowedUrlError
from tracecat.outbound import create_outbound_http_client
from tracecat.service import BaseWorkspaceService

PROBE_TIMEOUT_SECONDS = 5.0
STATUS_CACHE_TTL_SECONDS = 120
# A refresh within this window of the last check returns that check, so a
# caller looping on refresh can't spend the org's provider rate limit.
MIN_REFRESH_INTERVAL_SECONDS = 15
_STATUS_CACHE_MAX_ENTRIES = 1024
# Large gateways (OpenRouter) list a few hundred models; past this the listing
# is not checked for the model.
_MAX_MODEL_LIST_BYTES = 4 * 1024 * 1024

_OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_API_VERSION = "2023-06-01"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_MISTRAL_DEFAULT_BASE_URL = "https://api.mistral.ai/v1"

# Providers whose credential check needs a token exchange or a cloud SDK call.
# Tracecat confirms their credentials exist but does not probe them.
_UNPROBED_PROVIDERS = frozenset({"bedrock", "vertex_ai", "azure_openai", "azure_ai"})

type _CacheKey = tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]

# Per process. Keyed by a hash of the credentials, so a rotated key is
# re-checked at once; a stale entry otherwise lives for the TTL.
_status_cache = TTLCache[_CacheKey, ModelStatusRead](
    maxsize=_STATUS_CACHE_MAX_ENTRIES, ttl=STATUS_CACHE_TTL_SECONDS
)
# One probe in flight per key: concurrent checks share it, so the refresh floor
# holds however many callers arrive at once.
_inflight: dict[_CacheKey, asyncio.Task[ModelErrorDetail | None]] = {}


# The key each directly probed provider needs; without it no request can work.
_REQUIRED_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def credentials_incomplete(provider: str, credentials: dict[str, str]) -> bool:
    """Whether stored credentials lack what every model request needs.

    Mirrors the gateway's own "Provider credentials incomplete" checks: the
    provider's API key, or a gateway provider's base URL and required key.
    """
    if key := _REQUIRED_KEYS.get(provider):
        return not credentials.get(key)
    if is_gateway_provider(provider):
        spec = GATEWAY_PROVIDER_SPECS[provider]
        runtime = resolve_gateway_provider_config(provider, credentials)
        if runtime is None or not runtime.base_url:
            return True
        return spec.requires_api_key and not runtime.api_key
    return False


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """One authenticated GET that proves the key works for the model."""

    url: str
    headers: dict[str, str]
    # For a list endpoint, the model ID the list must contain. A list endpoint
    # answers 404 only for a wrong base URL, not a missing model.
    listed_model: str | None = None


def _bare_model_name(provider: str, model_name: str) -> str:
    """Drop a LiteLLM-style ``provider/`` prefix from a catalog model name."""
    prefix = f"{provider}/"
    return model_name.removeprefix(prefix)


def build_probe_target(
    provider: str, model_name: str, credentials: dict[str, str]
) -> ProbeTarget | None:
    """The cheapest authenticated request for a provider, or None if it has none.

    Retrieving one model costs nothing and proves both the key and access to
    that model. OpenAI-compatible gateways list their models instead. Check
    :func:`credentials_incomplete` first; None here never means a missing key.
    """
    model = quote(_bare_model_name(provider, model_name), safe="")
    match provider:
        case "openai":
            if not (api_key := credentials.get("OPENAI_API_KEY")):
                return None
            base = credentials.get("OPENAI_BASE_URL") or _OPENAI_DEFAULT_BASE_URL
            return ProbeTarget(
                url=f"{base.rstrip('/')}/models/{model}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        case "anthropic":
            if not (api_key := credentials.get("ANTHROPIC_API_KEY")):
                return None
            base = strip_openai_version_suffix(
                credentials.get("ANTHROPIC_BASE_URL") or _ANTHROPIC_DEFAULT_BASE_URL
            )
            return ProbeTarget(
                url=f"{base}/v1/models/{model}",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": _ANTHROPIC_API_VERSION,
                },
            )
        case "gemini":
            if not (api_key := credentials.get("GEMINI_API_KEY")):
                return None
            return ProbeTarget(
                url=f"{_GEMINI_BASE_URL}/models/{model}",
                headers={"x-goog-api-key": api_key},
            )
        case "mistral":
            if not (api_key := credentials.get("MISTRAL_API_KEY")):
                return None
            base = credentials.get("MISTRAL_BASE_URL") or _MISTRAL_DEFAULT_BASE_URL
            return ProbeTarget(
                url=f"{base.rstrip('/')}/models/{model}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        case _ if is_gateway_provider(provider):
            runtime = resolve_gateway_provider_config(provider, credentials)
            if runtime is None or not runtime.base_url:
                return None
            headers = (
                {"Authorization": f"Bearer {runtime.api_key}"}
                if runtime.api_key
                else {}
            )
            return ProbeTarget(
                url=f"{runtime.base_url}/models",
                headers=headers,
                listed_model=runtime.model_name
                or _bare_model_name(provider, model_name),
            )
        case _:
            return None


def _error_strings(body: bytes) -> tuple[str | None, str | None]:
    """The machine-readable ``error.type`` and ``error.code`` of a body."""
    match parse_bounded_error_body(body):
        case {"error": {**error}}:
            error_type = error.get("type")
            error_code = error.get("code")
            return (
                error_type if isinstance(error_type, str) else None,
                error_code if isinstance(error_code, str) else None,
            )
        case _:
            return None, None


async def _bounded_body(response: httpx.Response, limit: int) -> bytes:
    """Read at most ``limit + 1`` decoded bytes, so an oversized body is
    detectable but never held, even when one decompressed chunk is huge."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk[: limit + 1 - len(body)])
        if len(body) > limit:
            break
    return bytes(body)


def _lists_model(body: bytes, model_id: str) -> bool:
    """Whether a ``/models`` listing contains ``model_id``.

    A listing that is truncated or not the OpenAI shape proves nothing, so it
    counts as listed: the key itself was accepted.
    """
    if len(body) > _MAX_MODEL_LIST_BYTES:
        return True
    try:
        payload = orjson.loads(body)
    except orjson.JSONDecodeError:
        return True
    match payload:
        case {"data": [*items]}:
            pass
        case [*items]:
            pass
        case _:
            return True
    ids = {item.get("id") for item in items if isinstance(item, dict)}
    return model_id in ids


def _request_id(headers: httpx.Headers) -> str | None:
    return headers.get("x-request-id") or headers.get("request-id")


async def probe_model(
    target: ProbeTarget, *, provider: str, model: str
) -> ModelErrorDetail | None:
    """Run one probe. Returns None when the provider accepted it.

    Goes through the shared outbound client, so the deployment's egress policy
    and private-network rules apply exactly as they do to model requests.
    """
    try:
        async with (
            create_outbound_http_client(
                origin_url=target.url, timeout=PROBE_TIMEOUT_SECONDS
            ) as client,
            client.stream("GET", target.url, headers=target.headers) as response,
        ):
            body = b""
            if not response.is_success:
                body = await _bounded_body(response, MAX_LLM_ERROR_BODY_BYTES)
            elif target.listed_model is not None:
                body = await _bounded_body(response, _MAX_MODEL_LIST_BYTES)
    except (httpx.TransportError, DisallowedUrlError) as exc:
        logger.info(
            "Model status probe could not reach the provider",
            provider=provider,
            error_type=type(exc).__name__,
        )
        return model_error_detail(
            None, provider=provider, model=model, provider_unreachable=True
        )
    if response.is_success:
        if target.listed_model is None or _lists_model(body, target.listed_model):
            return None
        return ModelErrorDetail(
            code=ModelErrorCode.MODEL_UNAVAILABLE, provider=provider, model=model
        )
    error_type, error_code = _error_strings(body)
    detail = model_error_detail(
        None,
        provider=provider,
        model=model,
        status_code=response.status_code,
        request_id=_request_id(response.headers),
        provider_error_code=error_code,
        provider_error_type=error_type,
    )
    if (
        target.listed_model is not None
        and detail.code is ModelErrorCode.MODEL_UNAVAILABLE
    ):
        detail = detail.model_copy(update={"code": ModelErrorCode.PROVIDER_UNREACHABLE})
    return detail


def _credentials_fingerprint(credentials: dict[str, str]) -> str:
    return hashlib.sha256(
        orjson.dumps(credentials, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()


def clear_status_cache() -> None:
    """Drop every cached status. For tests."""
    _status_cache.clear()


class ModelStatusService(BaseWorkspaceService):
    """Checks whether the workspace can reach a model with its credentials."""

    service_name = "model_status"

    async def get_status(
        self,
        *,
        catalog_id: uuid.UUID | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        preset: bool = False,
        refresh: bool = False,
    ) -> ModelStatusRead:
        """Check one model, by catalog ID or by provider and name.

        Args:
            catalog_id: The model's catalog row.
            model_provider: Provider slug, when the catalog ID is unknown.
            model_name: Model name, when the catalog ID is unknown.
            preset: The chat runs this model through an agent preset, whose
                credentials fall back to workspace and then organization keys.
            refresh: Skip the cache, unless the last check is very recent.

        Raises:
            TracecatNotFoundError: If no catalog row matches.
        """
        org_id, workspace_id = self.organization_id, self.workspace_id
        row = await self._catalog_row(
            catalog_id=catalog_id,
            model_provider=model_provider,
            model_name=model_name,
        )
        provider, model = row.model_provider, row.model_name

        def status(
            problem: ModelErrorDetail | None, *, verified: bool = True
        ) -> ModelStatusRead:
            state: ModelStatusState = "ready" if verified else "unverified"
            if problem is not None:
                state = "problem"
            return ModelStatusRead(
                state=state,
                provider=provider,
                model=model,
                problem=problem,
                checked_at=datetime.now(UTC),
            )

        def problem(code: ModelErrorCode) -> ModelStatusRead:
            return status(ModelErrorDetail(code=code, provider=provider, model=model))

        access = AgentModelAccessService(session=self.session, role=self.role)
        if not await access.is_catalog_enabled(row.id, workspace_id=workspace_id):
            return problem(ModelErrorCode.MODEL_NOT_ENABLED)

        management = AgentManagementService(self.session, role=self.role)
        if provider in _UNPROBED_PROVIDERS:
            if await management.check_provider_credentials(provider) or (
                preset
                and await management.check_workspace_provider_credentials(provider)
            ):
                return status(None, verified=False)
            return problem(ModelErrorCode.CREDENTIALS_MISSING)

        credentials = await self._runtime_credentials(
            management, row.id, provider, preset=preset
        )
        if not credentials or credentials_incomplete(provider, credentials):
            return problem(ModelErrorCode.CREDENTIALS_MISSING)
        target = build_probe_target(provider, model, credentials)
        if target is None:
            return status(None, verified=False)

        key = (org_id, workspace_id, row.id, _credentials_fingerprint(credentials))
        if (cached := _status_cache.get(key)) is not None:
            age = (datetime.now(UTC) - cached.checked_at).total_seconds()
            if not refresh or age < MIN_REFRESH_INTERVAL_SECONDS:
                return cached
        if (task := _inflight.get(key)) is None:
            task = asyncio.create_task(
                probe_model(target, provider=provider, model=model)
            )
            _inflight[key] = task
            task.add_done_callback(lambda _: _inflight.pop(key, None))
        detail = await asyncio.shield(task)
        if detail is not None and detail.status_code == 403:
            # The key may lack only the permission to read model metadata,
            # which inference doesn't need; that says nothing about chat.
            result = status(None, verified=False)
        else:
            result = status(detail)
        _status_cache[key] = result
        return result

    async def _runtime_credentials(
        self,
        management: AgentManagementService,
        catalog_id: uuid.UUID,
        provider: str,
        *,
        preset: bool,
    ) -> dict[str, str] | None:
        """The credentials a chat on this model runs with.

        The organization default uses its catalog row's credentials only. A
        preset falls back to workspace, then organization provider keys, as
        ``AgentManagementService.with_preset_config`` does.
        """
        credentials = await management.get_catalog_credentials(catalog_id)
        if credentials is not None or not preset:
            return credentials
        return await management.get_workspace_runtime_provider_credentials(
            provider
        ) or await management.get_runtime_provider_credentials(provider)

    async def _catalog_row(
        self,
        *,
        catalog_id: uuid.UUID | None,
        model_provider: str | None,
        model_name: str | None,
    ) -> AgentCatalog:
        visible = sa.or_(
            AgentCatalog.organization_id.is_(None),
            AgentCatalog.organization_id == self.organization_id,
        )
        if catalog_id is not None:
            row = await self.session.scalar(
                select(AgentCatalog).where(AgentCatalog.id == catalog_id, visible)
            )
        elif model_provider and model_name:
            # Prefer the row the runtime would pick. When none is enabled
            # here, any visible match lets the check say "not enabled".
            enabled_id = await AgentCatalogService(
                session=self.session
            ).resolve_catalog_id_by_model(
                org_id=self.organization_id,
                model_provider=model_provider,
                model_name=model_name,
                workspace_id=self.workspace_id,
            )
            match_row = sa.and_(
                AgentCatalog.model_provider == model_provider,
                AgentCatalog.model_name == model_name,
            )
            stmt = select(AgentCatalog).where(
                AgentCatalog.id == enabled_id if enabled_id is not None else match_row,
                visible,
            )
            row = await self.session.scalar(stmt.limit(1))
        else:
            row = None
        if row is None:
            raise TracecatNotFoundError("Model not found")
        return row
