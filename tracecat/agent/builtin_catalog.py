from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson

from tracecat.agent.types import ModelSourceType

PLATFORM_CATALOG_SCHEMA_VERSION = 1
PLATFORM_CATALOG_PATH = Path(__file__).with_name("platform_catalog.json")
PLATFORM_CATALOG_RELATIVE_PATH = Path("tracecat/agent/platform_catalog.json")

_PROVIDER_TO_SOURCE_TYPE: dict[str, ModelSourceType] = {
    "openai": ModelSourceType.OPENAI,
    "anthropic": ModelSourceType.ANTHROPIC,
    "gemini": ModelSourceType.GEMINI,
    "bedrock": ModelSourceType.BEDROCK,
    "vertex_ai": ModelSourceType.VERTEX_AI,
    "azure_openai": ModelSourceType.AZURE_OPENAI,
    "azure_ai": ModelSourceType.AZURE_AI,
}
_PROVIDER_PREFIX_HINTS = {
    "azure_openai": "azure",
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
_BLOCKED_MODEL_PREFIXES = (
    "claude-v1",
    "claude-v2",
    "claude-instant",
    "claude-3",
    "claude-3-5",
    "claude-3-7",
    "anthropic.claude-v1",
    "anthropic.claude-v2",
    "anthropic.claude-instant",
    "anthropic.claude-3",
    "anthropic.claude-3-5",
    "anthropic.claude-3-7",
    "gpt-3.5",
    "gpt-4",
    "gpt-4o",
    "gpt-4.1",
    "chatgpt-4o",
    "o1",
    "o3",
    "o4",
)
_BLOCKED_MODEL_PATTERNS = (
    r"(^|[./:])gpt-(audio|realtime)($|[-./:])",
    r"(^|[./:])gpt-4o(-mini)?-(transcribe|tts)($|[-./:])",
    r"(^|[./:])gpt-image-1(\.5)?($|[-./:])",
    r"(^|[./:])computer-use-preview($|[-./:])",
    r"(^|[./:])gemini-(embedding|live|robotics)($|[-./:])",
    r"(^|[./:])gemini-.*-(image|tts)($|[-./:])",
    r"(^|[./:])gemini-.*computer-use($|[-./:])",
    r"(^|[./:])gemini-.*(experimental|exp)($|[-./:])",
    r"(^|[./:])gemini-pro-vision($|[-./:])",
    r"(^|[./:])gemini-gemma($|[-./:])",
    r"(^|[./:])gemma-[^/]*($|[-./:])",
    r"(^|[./:])(imagen|veo|learnlm)($|[-./:])",
)


@dataclass(frozen=True, slots=True)
class BuiltInCatalogModel:
    agent_catalog_id: uuid.UUID
    source_type: ModelSourceType
    model_provider: str
    model_id: str
    display_name: str
    mode: str | None
    enableable: bool
    readiness_message: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BuiltInCatalogSnapshot:
    version: int
    content_hash: str
    models: dict[str, dict[str, Any]]


def _strip_provider_prefix(raw_model_id: str, upstream_provider: str) -> str:
    prefix = f"{upstream_provider}/"
    if raw_model_id.startswith(prefix):
        return raw_model_id.removeprefix(prefix)
    return raw_model_id


def _catalog_uuid(model_provider: str, model_id: str) -> uuid.UUID:
    digest = hashlib.sha256(
        f"platform:{model_provider}:{model_id}".encode()
    ).hexdigest()
    return uuid.UUID(digest[:32])


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


def _is_builtin_catalog_model_blocked(*, raw_model_id: str, model_name: str) -> bool:
    lowered_raw_model_id = raw_model_id.lower()
    lowered_model_name = model_name.lower()
    if any(
        re.search(rf"(^|[./:]){re.escape(prefix)}($|[-./:])", candidate)
        for prefix in _BLOCKED_MODEL_PREFIXES
        for candidate in (lowered_raw_model_id, lowered_model_name)
    ):
        return True
    return any(
        re.search(pattern, candidate)
        for pattern in _BLOCKED_MODEL_PATTERNS
        for candidate in (lowered_raw_model_id, lowered_model_name)
    )


@lru_cache(maxsize=1)
def _load_platform_catalog_snapshot() -> BuiltInCatalogSnapshot:
    try:
        raw_payload = PLATFORM_CATALOG_PATH.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(
            f"Platform catalog snapshot not found at {PLATFORM_CATALOG_PATH}"
        ) from exc
    try:
        payload = orjson.loads(raw_payload)
    except orjson.JSONDecodeError as exc:
        raise ValueError(
            f"Platform catalog snapshot at {PLATFORM_CATALOG_PATH} is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Platform catalog snapshot must be a JSON object")
    if payload.get("version") != PLATFORM_CATALOG_SCHEMA_VERSION:
        raise ValueError(
            "Platform catalog snapshot version mismatch: "
            f"expected {PLATFORM_CATALOG_SCHEMA_VERSION}, got {payload.get('version')}"
        )
    models = payload.get("models")
    if not isinstance(models, dict):
        raise ValueError("Platform catalog snapshot must define a 'models' object")
    normalized_models: dict[str, dict[str, Any]] = {}
    for raw_model_id, item in models.items():
        if not isinstance(raw_model_id, str):
            raise ValueError("Platform catalog model IDs must be strings")
        if not isinstance(item, dict):
            raise ValueError(
                f"Platform catalog entry for {raw_model_id!r} must be an object"
            )
        normalized_models[raw_model_id] = dict(item)
    return BuiltInCatalogSnapshot(
        version=PLATFORM_CATALOG_SCHEMA_VERSION,
        content_hash=hashlib.sha256(raw_payload).hexdigest(),
        models=normalized_models,
    )


@lru_cache(maxsize=1)
def get_builtin_catalog_metadata() -> dict[str, Any]:
    snapshot = _load_platform_catalog_snapshot()
    return {
        "catalog_version": snapshot.version,
        "catalog_sha256": snapshot.content_hash,
        "catalog_path": str(PLATFORM_CATALOG_RELATIVE_PATH),
    }


def _resolve_snapshot_provider(
    *,
    raw_model_id: str,
    item: dict[str, Any],
) -> tuple[str, str, ModelSourceType]:
    provider = item.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError(
            f"Platform catalog entry {raw_model_id!r} is missing a valid provider"
        )
    canonical_provider = provider.strip()
    if not (source_type := _PROVIDER_TO_SOURCE_TYPE.get(canonical_provider)):
        raise ValueError(
            f"Platform catalog entry {raw_model_id!r} has unsupported provider "
            f"{canonical_provider!r}"
        )
    upstream_provider_value = item.get("litellm_provider")
    if upstream_provider_value is None:
        upstream_provider = _PROVIDER_PREFIX_HINTS.get(
            canonical_provider, canonical_provider
        )
    elif isinstance(upstream_provider_value, str) and upstream_provider_value.strip():
        upstream_provider = upstream_provider_value.strip()
    else:
        raise ValueError(
            f"Platform catalog entry {raw_model_id!r} has invalid litellm_provider"
        )
    return canonical_provider, upstream_provider, source_type


def _resolve_display_name(
    *,
    item: dict[str, Any],
    model_id: str,
) -> str:
    if isinstance(display_name := item.get("display_name"), str):
        if normalized_display_name := display_name.strip():
            return normalized_display_name
    return model_id


def _resolve_model_id(
    *,
    raw_model_id: str,
    item: dict[str, Any],
    upstream_provider: str,
) -> str:
    if isinstance(model_name := item.get("model_name"), str):
        if normalized_model_name := model_name.strip():
            return normalized_model_name
    return _strip_provider_prefix(raw_model_id, upstream_provider)


@lru_cache(maxsize=1)
def get_builtin_catalog_models() -> tuple[BuiltInCatalogModel, ...]:
    snapshot = _load_platform_catalog_snapshot()
    snapshot_metadata = get_builtin_catalog_metadata()

    rows: list[BuiltInCatalogModel] = []
    seen_identities: set[tuple[str, str]] = set()
    for raw_model_id, item in snapshot.models.items():
        model_provider, upstream_provider, source_type = _resolve_snapshot_provider(
            raw_model_id=raw_model_id,
            item=item,
        )
        model_id = _resolve_model_id(
            raw_model_id=raw_model_id,
            item=item,
            upstream_provider=upstream_provider,
        )
        identity = (model_provider, model_id)
        if identity in seen_identities:
            raise ValueError(
                "Platform catalog snapshot contains duplicate provider/model_name "
                f"identity {identity!r}"
            )
        seen_identities.add(identity)
        model_name = _resolve_display_name(
            item=item,
            model_id=model_id,
        )
        if _is_builtin_catalog_model_blocked(
            raw_model_id=raw_model_id,
            model_name=model_name,
        ):
            continue
        mode = item.get("mode")
        enableable, readiness_message = _is_agent_enableable(item)
        metadata = {
            **item,
            "upstream_provider": upstream_provider,
            "upstream_model_id": raw_model_id,
            **snapshot_metadata,
        }

        rows.append(
            BuiltInCatalogModel(
                agent_catalog_id=_catalog_uuid(model_provider, model_id),
                source_type=source_type,
                model_provider=model_provider,
                model_id=model_id,
                display_name=model_name,
                mode=mode if isinstance(mode, str) else None,
                enableable=enableable,
                readiness_message=readiness_message,
                metadata=metadata,
            )
        )

    return tuple(
        sorted(
            rows,
            key=lambda row: (row.model_provider, row.display_name.lower()),
        )
    )


@lru_cache(maxsize=1)
def get_builtin_catalog_by_id() -> dict[uuid.UUID, BuiltInCatalogModel]:
    return {row.agent_catalog_id: row for row in get_builtin_catalog_models()}


@lru_cache(maxsize=1)
def get_builtin_catalog_by_provider() -> dict[str, tuple[BuiltInCatalogModel, ...]]:
    grouped: dict[str, list[BuiltInCatalogModel]] = {}
    for row in get_builtin_catalog_models():
        grouped.setdefault(row.model_provider, []).append(row)
    return {provider: tuple(rows) for provider, rows in grouped.items()}
