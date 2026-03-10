from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson

from tracecat.agent.types import ModelSourceType

LITELLM_PINNED_VERSION = "1.82.1"
_CATALOG_ROOT = Path(__file__).resolve().parent / "data" / "litellm" / "v1.82.1"
_MODEL_PRICING_PATH = _CATALOG_ROOT / "model_prices_and_context_window.json"
_PROVIDER_ENDPOINTS_PATH = _CATALOG_ROOT / "provider_endpoints_support.json"

_UPSTREAM_TO_SOURCE_TYPE: dict[str, ModelSourceType] = {
    "openai": ModelSourceType.OPENAI,
    "anthropic": ModelSourceType.ANTHROPIC,
    "gemini": ModelSourceType.GEMINI,
    "bedrock": ModelSourceType.BEDROCK,
    "bedrock_converse": ModelSourceType.BEDROCK,
    "vertex_ai": ModelSourceType.VERTEX_AI,
    "azure": ModelSourceType.AZURE_OPENAI,
    "azure_ai": ModelSourceType.AZURE_AI,
}
_AGENT_SUPPORTED_MODES = {
    "chat",
    "completion",
    "text_completion",
    "responses",
    "response",
}
_AGENT_SUPPORTED_ENDPOINTS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
}


@dataclass(frozen=True, slots=True)
class BuiltInCatalogModel:
    catalog_ref: str
    source_type: ModelSourceType
    source_name: str
    model_name: str
    model_provider: str
    runtime_provider: str
    display_name: str
    raw_model_id: str
    mode: str | None
    enableable: bool
    readiness_message: str | None
    metadata: dict[str, Any]


def _load_json(path: Path) -> dict[str, Any]:
    return orjson.loads(path.read_bytes())


def _strip_provider_prefix(raw_model_id: str, upstream_provider: str) -> str:
    prefix = f"{upstream_provider}/"
    if raw_model_id.startswith(prefix):
        return raw_model_id.removeprefix(prefix)
    return raw_model_id


def _catalog_ref(runtime_provider: str, raw_model_id: str) -> str:
    digest = hashlib.sha256(f"{runtime_provider}:{raw_model_id}".encode()).hexdigest()[
        :16
    ]
    return f"builtin:{runtime_provider}:{digest}:{raw_model_id}"


def _is_agent_enableable(metadata: dict[str, Any]) -> tuple[bool, str | None]:
    mode = metadata.get("mode")
    if isinstance(mode, str) and mode in _AGENT_SUPPORTED_MODES:
        return True, None
    supported_endpoints = metadata.get("supported_endpoints")
    if isinstance(supported_endpoints, list) and any(
        isinstance(endpoint, str) and endpoint in _AGENT_SUPPORTED_ENDPOINTS
        for endpoint in supported_endpoints
    ):
        return True, None
    return False, "Only chat-capable models can be enabled for agents."


@lru_cache(maxsize=1)
def get_builtin_catalog_models() -> tuple[BuiltInCatalogModel, ...]:
    model_payload = _load_json(_MODEL_PRICING_PATH)
    provider_payload = _load_json(_PROVIDER_ENDPOINTS_PATH)
    provider_support = provider_payload.get("providers")
    if not isinstance(provider_support, dict):
        provider_support = {}

    rows: list[BuiltInCatalogModel] = []
    for raw_model_id, item in model_payload.items():
        if not isinstance(raw_model_id, str) or not isinstance(item, dict):
            continue
        upstream_provider = item.get("litellm_provider")
        if not isinstance(upstream_provider, str):
            continue
        if not (source_type := _UPSTREAM_TO_SOURCE_TYPE.get(upstream_provider)):
            continue

        runtime_provider = source_type.value
        model_name = _strip_provider_prefix(raw_model_id, upstream_provider)
        provider_meta = provider_support.get(upstream_provider)
        mode = item.get("mode")
        enableable, readiness_message = _is_agent_enableable(item)
        metadata = {
            **item,
            "upstream_provider": upstream_provider,
            "upstream_model_id": raw_model_id,
            "litellm_version": LITELLM_PINNED_VERSION,
        }
        if isinstance(provider_meta, dict):
            metadata["provider_endpoints"] = provider_meta.get("endpoints")
            metadata["provider_docs_url"] = provider_meta.get("url")

        rows.append(
            BuiltInCatalogModel(
                catalog_ref=_catalog_ref(runtime_provider, raw_model_id),
                source_type=source_type,
                source_name=runtime_provider.replace("_", " ").title(),
                model_name=model_name,
                model_provider=runtime_provider,
                runtime_provider=runtime_provider,
                display_name=model_name,
                raw_model_id=raw_model_id,
                mode=mode if isinstance(mode, str) else None,
                enableable=enableable,
                readiness_message=readiness_message,
                metadata=metadata,
            )
        )

    return tuple(
        sorted(
            rows,
            key=lambda row: (row.runtime_provider, row.display_name.lower()),
        )
    )


@lru_cache(maxsize=1)
def get_builtin_catalog_by_ref() -> dict[str, BuiltInCatalogModel]:
    return {row.catalog_ref: row for row in get_builtin_catalog_models()}


@lru_cache(maxsize=1)
def get_builtin_catalog_by_provider() -> dict[str, tuple[BuiltInCatalogModel, ...]]:
    grouped: dict[str, list[BuiltInCatalogModel]] = {}
    for row in get_builtin_catalog_models():
        grouped.setdefault(row.runtime_provider, []).append(row)
    return {provider: tuple(rows) for provider, rows in grouped.items()}
