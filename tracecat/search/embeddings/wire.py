"""Translate the four provider wire formats into one ordered result.

This module owns payloads, authentication and response parsing. It does not make
HTTP calls, select providers, access the database or validate vector contents.
"""

import orjson
from pydantic import BaseModel, ConfigDict, Field

from tracecat.search.embeddings.bedrock import request_headers
from tracecat.search.embeddings.catalog import API_KEY_FIELDS
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    ModelSpec,
    ProviderResult,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingRequest


class _StrictResponse(BaseModel):
    model_config = ConfigDict(strict=True)


class _IndexedVector(_StrictResponse):
    index: int = Field(ge=0)
    embedding: list[float]


class _Usage(_StrictResponse):
    prompt_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class _OpenAIResponse(_StrictResponse):
    model: str
    data: list[_IndexedVector]
    usage: _Usage


class _GeminiVector(_StrictResponse):
    values: list[float]


class _GeminiUsage(_StrictResponse):
    promptTokenCount: int = Field(ge=0)


class _GeminiResponse(_StrictResponse):
    embeddings: list[_GeminiVector]
    usageMetadata: _GeminiUsage | None = None


class _BedrockResponse(_StrictResponse):
    embedding: list[float]
    inputTextTokenCount: int = Field(ge=0)


class _OllamaResponse(_StrictResponse):
    model: str
    embeddings: list[list[float]]
    prompt_eval_count: int | None = Field(default=None, ge=0)


async def encode_request(
    spec: ModelSpec,
    credential: ResolvedCredential,
    request: EmbeddingRequest,
) -> tuple[dict[str, str], bytes]:
    """Return headers and exact bytes to send (and sign for Bedrock)."""
    texts = [item.text for item in request.items]
    match spec.provider:
        case "openai" | "vllm" | "ollama":
            options = (
                {"truncate": False}
                if spec.provider == "ollama"
                else {"encoding_format": "float"}
            )
            body = orjson.dumps({"model": spec.model, "input": texts, **options})
        case "gemini":
            # Symmetric task: query and document inputs follow the same recipe.
            body = orjson.dumps(
                {
                    "requests": [
                        {
                            "model": f"models/{spec.model}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": "SEMANTIC_SIMILARITY",
                            "outputDimensionality": spec.dimensions,
                        }
                        for text in texts
                    ]
                }
            )
        case "bedrock":
            body = orjson.dumps(
                {
                    "inputText": texts[0],
                    "dimensions": spec.dimensions,
                    "normalize": True,
                }
            )
            return await request_headers(
                credential.values, request.scope, spec.endpoint, body
            ), body
    headers = {"Content-Type": "application/json"}
    key = credential.values.get(API_KEY_FIELDS[spec.provider], "")
    if spec.provider == "gemini":
        headers["x-goog-api-key"] = key
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return headers, body


def decode_response(spec: ModelSpec, raw: bytes) -> ProviderResult:
    """Check wire identity/order and normalize; vector checks belong to the client."""
    match spec.provider:
        case "openai" | "vllm":
            response = _OpenAIResponse.model_validate_json(raw)
            ordered = sorted(response.data, key=lambda item: item.index)
            if (
                response.model != spec.model
                or [item.index for item in ordered] != list(range(len(ordered)))
                or response.usage.total_tokens < response.usage.prompt_tokens
            ):
                raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
            return ProviderResult(
                [item.embedding for item in ordered],
                response.usage.prompt_tokens,
                response.usage.total_tokens,
            )
        case "ollama":
            response = _OllamaResponse.model_validate_json(raw)
            # An omitted tag is :latest. Explicit tags must match exactly.
            expected = spec.model if ":" in spec.model else f"{spec.model}:latest"
            actual = (
                response.model if ":" in response.model else f"{response.model}:latest"
            )
            if actual != expected:
                raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
            return ProviderResult(
                response.embeddings,
                response.prompt_eval_count,
                response.prompt_eval_count,
            )
        case "gemini":
            response = _GeminiResponse.model_validate_json(raw)
            tokens = (
                response.usageMetadata.promptTokenCount
                if response.usageMetadata
                else None
            )
            return ProviderResult(
                [item.values for item in response.embeddings], tokens, tokens
            )
        case "bedrock":
            response = _BedrockResponse.model_validate_json(raw)
            return ProviderResult(
                [response.embedding],
                response.inputTextTokenCount,
                response.inputTextTokenCount,
            )
