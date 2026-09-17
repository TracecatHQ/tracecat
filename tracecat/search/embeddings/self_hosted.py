"""Known self-hosted models and endpoints, using existing agent connections.

Only choose exact model names discovered into the permitted catalog. A chat
connection alone does not establish that its server has an embedding model.
"""

from collections.abc import Collection
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from tracecat.agent.gateway_providers import resolve_gateway_provider_config
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingProvider,
    ModelSpec,
)

# MiniLM embeds queries and documents without task-specific prefixes. Budget 240
# UTF-8 bytes, leaving room for special tokens within its 256-token trained limit.
# Sources: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
# and https://ollama.com/library/all-minilm (22m is the default L6 model).
_MINILM = ModelSpec(
    provider="ollama",
    model="all-minilm:22m",
    dimensions=384,
    endpoint="",
    tokenizer="utf8-bytes:v1",
    input_token_limit=240,
    input_character_limit=240,
)
SELF_HOSTED_MODELS = (
    _MINILM,
    replace(_MINILM, model="all-minilm:latest"),
    replace(_MINILM, model="all-minilm"),
    replace(_MINILM, provider="vllm", model="sentence-transformers/all-MiniLM-L6-v2"),
)


def select_self_hosted_model(
    provider: EmbeddingProvider,
    allowed_models: Collection[str],
    credentials: dict[str, str],
) -> ModelSpec | None:
    """Select in a fixed order and bind to the configured server, never public AI."""
    spec = next(
        (
            spec
            for spec in SELF_HOSTED_MODELS
            if spec.provider == provider and spec.model in allowed_models
        ),
        None,
    )
    if spec is None:
        return None
    runtime = resolve_gateway_provider_config(provider, credentials)
    if runtime is None or not runtime.base_url:
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
    url = urlsplit(runtime.base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
    path = url.path.rstrip("/")
    if not path.endswith("/v1"):
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
    # Ollama's native endpoint lets us explicitly disable silent truncation.
    # Preserve any reverse-proxy prefix before /v1.
    path = path[:-3] + "/api/embed" if provider == "ollama" else path + "/embeddings"
    return replace(spec, endpoint=urlunsplit((url.scheme, url.netloc, path, "", "")))
