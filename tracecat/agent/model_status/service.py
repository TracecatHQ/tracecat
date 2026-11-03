"""Cheap, cached connectivity checks for a chat's selected model."""

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
from tracecat.agent.diagnostics import parse_bounded_error_body
from tracecat.agent.gateway_providers import (
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
_STATUS_CACHE_MAX_ENTRIES = 1024

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


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """One authenticated GET that proves the key works for the model."""

    url: str
    headers: dict[str, str]
    # A list endpoint answers 404 only for a wrong base URL, not a missing model.
    lists_models: bool = False


def _bare_model_name(provider: str, model_name: str) -> str:
    """Drop a LiteLLM-style ``provider/`` prefix from a catalog model name."""
    prefix = f"{provider}/"
    return model_name.removeprefix(prefix)


def build_probe_target(
    provider: str, model_name: str, credentials: dict[str, str]
) -> ProbeTarget | None:
    """The cheapest authenticated request for a provider, or None if it has none.

    Retrieving one model costs nothing and proves both the key and access to
    that model. OpenAI-compatible gateways list their models instead.
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
                url=f"{runtime.base_url}/models", headers=headers, lists_models=True
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
        async with create_outbound_http_client(
            origin_url=target.url, timeout=PROBE_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(target.url, headers=target.headers)
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
        return None
    error_type, error_code = _error_strings(response.content)
    detail = model_error_detail(
        None,
        provider=provider,
        model=model,
        status_code=response.status_code,
        request_id=_request_id(response.headers),
        provider_error_code=error_code,
        provider_error_type=error_type,
    )
    if target.lists_models and detail.code is ModelErrorCode.MODEL_UNAVAILABLE:
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
        refresh: bool = False,
    ) -> ModelStatusRead:
        """Check one model, by catalog ID or by provider and name.

        Args:
            catalog_id: The model's catalog row.
            model_provider: Provider slug, when the catalog ID is unknown.
            model_name: Model name, when the catalog ID is unknown.
            refresh: Skip the cache.

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
            if await management.check_provider_credentials(provider):
                return status(None, verified=False)
            return problem(ModelErrorCode.CREDENTIALS_MISSING)

        credentials = await management.get_catalog_credentials(row.id)
        if not credentials:
            return problem(ModelErrorCode.CREDENTIALS_MISSING)
        target = build_probe_target(provider, model, credentials)
        if target is None:
            return status(None, verified=False)

        key = (org_id, workspace_id, row.id, _credentials_fingerprint(credentials))
        if not refresh and (cached := _status_cache.get(key)) is not None:
            return cached
        result = status(await probe_model(target, provider=provider, model=model))
        _status_cache[key] = result
        return result

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
