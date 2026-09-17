"""One bounded provider call; no database, table, workflow or retry machinery."""

import asyncio
import hashlib
import math
import struct
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import orjson
from pydantic import ValidationError

from tracecat.outbound import OutboundRequestDenied, create_outbound_http_client
from tracecat.search.embeddings.bedrock import request_headers
from tracecat.search.embeddings.catalog import ByteTokenCounter, EmbeddingTokenCounter
from tracecat.search.embeddings.types import (
    BedrockResponse,
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingErrorCode,
    GeminiResponse,
    ModelSpec,
    OllamaResponse,
    PinnedConfiguration,
    ProviderResponse,
    ProviderUsage,
    ProviderVector,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingRequest, EmbeddingResult


def _check_token_budget(request: EmbeddingRequest, spec: ModelSpec) -> None:
    # Tokenization is CPU work. Run it off the event loop and stop as soon as a
    # budget is exceeded rather than processing the rest of an invalid batch.
    counter = (
        EmbeddingTokenCounter() if spec.provider == "openai" else ByteTokenCounter()
    )
    total = 0
    for item in request.items:
        count = counter.count_tokens(item.text)
        total += count
        if count > spec.input_token_limit or total > spec.batch_token_limit:
            raise EmbeddingError(EmbeddingErrorCode.INPUT_INVALID)


def _retry_after(value: str | None) -> float | None:
    if value is None or len(value) > 128:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and 0 <= seconds <= 604800 else None


def _status_error(response: httpx.Response) -> EmbeddingError:
    match response.status_code:
        case 401 | 403:
            code = EmbeddingErrorCode.CREDENTIAL_INVALID
        case 429:
            code = EmbeddingErrorCode.RATE_LIMITED
        case 408 | 409:
            code = EmbeddingErrorCode.UNAVAILABLE
        case status if status >= 500:
            code = EmbeddingErrorCode.UNAVAILABLE
        case _:
            code = EmbeddingErrorCode.CONFIGURATION_INVALID
    return EmbeddingError(code, _retry_after(response.headers.get("retry-after")))


def _validate_response(
    response: ProviderResponse,
    request: EmbeddingRequest,
    configuration: PinnedConfiguration,
) -> EmbeddingBatch:
    if (
        response.model != configuration.spec.model
        or len(response.data) != len(request.items)
        or response.usage.total_tokens < response.usage.prompt_tokens
    ):
        raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
    by_index = {item.index: item.embedding for item in response.data}
    if set(by_index) != set(range(len(request.items))):
        raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
    results = []
    for index, item in enumerate(request.items):
        vector = by_index[index]
        if len(vector) != request.dimensions:
            raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
        # PostgreSQL stores float32. Check the value that storage will actually see.
        converted = tuple(struct.unpack("f", struct.pack("f", x))[0] for x in vector)
        if not all(math.isfinite(x) for x in converted) or not any(converted):
            raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
        results.append(
            EmbeddingResult(
                item.ordinal, item.input_hash, request.config_version, converted
            )
        )
    return EmbeddingBatch(
        tuple(results), response.usage.prompt_tokens, response.usage.total_tokens
    )


class EmbeddingClient:
    """Use the caller's cloud client and guarded self-hosted clients; never retry."""

    def __init__(self, http: httpx.AsyncClient, *, timeout: float = 30.0):
        self.http = http
        self.timeout = timeout

    async def embed(
        self,
        configuration: PinnedConfiguration,
        credential: ResolvedCredential,
        request: EmbeddingRequest,
    ) -> EmbeddingBatch:
        """Embed exact inputs and return only validated, correctly mapped vectors.

        Errors are rebuilt from typed metadata after leaving exception handlers;
        provider bodies and exception chains are never returned or logged.
        """
        try:
            spec = configuration.spec
            if (
                request.config_version != configuration.version
                or request.dimensions != spec.dimensions
            ):
                raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
            if not 1 <= len(request.items) <= spec.batch_size_limit:
                raise EmbeddingError(EmbeddingErrorCode.INPUT_INVALID)
            ordinals: set[int] = set()
            for item in request.items:
                if (
                    item.ordinal < 0
                    or item.ordinal in ordinals
                    or not item.text.strip()
                    or len(item.text) > spec.input_character_limit
                    or hashlib.sha256(item.text.encode()).hexdigest() != item.input_hash
                ):
                    raise EmbeddingError(EmbeddingErrorCode.INPUT_INVALID)
                ordinals.add(item.ordinal)
            async with asyncio.timeout(self.timeout):
                await asyncio.to_thread(_check_token_budget, request, spec)
                return await self._embed(configuration, credential, request)

        except EmbeddingError as exc:
            error = EmbeddingError(exc.code, exc.retry_after)
        except OutboundRequestDenied:
            error = EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
        except UnicodeError:
            error = EmbeddingError(EmbeddingErrorCode.INPUT_INVALID)
        except (TimeoutError, httpx.TimeoutException):
            error = EmbeddingError(EmbeddingErrorCode.TIMEOUT)
        except (ValidationError, OverflowError, struct.error):
            error = EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
        except Exception:
            # Includes transport and tokenizer initialization failures. Never retain
            # their messages: either can carry request data or credential values.
            error = EmbeddingError(EmbeddingErrorCode.UNAVAILABLE)
        raise error

    async def _post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        *,
        self_hosted: bool = False,
    ) -> bytes:
        async with AsyncExitStack() as stack:
            http = self.http
            if self_hosted:
                # Configured servers are caller-controlled destinations. Apply the
                # same DNS/IP policy and origin binding as agent model discovery.
                http = await stack.enter_async_context(
                    create_outbound_http_client(origin_url=url, timeout=self.timeout)
                )
            response = await stack.enter_async_context(
                http.stream(
                    "POST",
                    url,
                    headers=headers,
                    content=body,
                    timeout=self.timeout,
                    follow_redirects=False,
                )
            )
            if response.status_code != 200:
                raise _status_error(response)
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 4_000_000:
                    raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
            return bytes(data)

    async def _embed(
        self,
        configuration: PinnedConfiguration,
        credential: ResolvedCredential,
        request: EmbeddingRequest,
    ) -> EmbeddingBatch:
        spec = configuration.spec
        headers = {"Content-Type": "application/json"}
        match spec.provider:
            case "openai" | "vllm":
                if key := credential.api_key.get_secret_value():
                    headers["Authorization"] = f"Bearer {key}"
                body = orjson.dumps(
                    {
                        "model": spec.model,
                        "input": [item.text for item in request.items],
                        "encoding_format": "float",
                    }
                )
                parsed = ProviderResponse.model_validate_json(
                    await self._post(
                        spec.endpoint,
                        headers,
                        body,
                        self_hosted=spec.provider == "vllm",
                    )
                )
                return _validate_response(parsed, request, configuration)
            case "ollama":
                if key := credential.api_key.get_secret_value():
                    headers["Authorization"] = f"Bearer {key}"
                body = orjson.dumps(
                    {
                        "model": spec.model,
                        "input": [item.text for item in request.items],
                        "truncate": False,
                    }
                )
                response = OllamaResponse.model_validate_json(
                    await self._post(spec.endpoint, headers, body, self_hosted=True)
                )
                # An omitted Ollama tag means :latest. Other tags must match exactly.
                expected_model = (
                    spec.model if ":" in spec.model else f"{spec.model}:latest"
                )
                actual_model = (
                    response.model
                    if ":" in response.model
                    else f"{response.model}:latest"
                )
                if actual_model != expected_model:
                    raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
                tokens = response.prompt_eval_count
                vectors = [
                    ProviderVector(index=i, embedding=vector)
                    for i, vector in enumerate(response.embeddings)
                ]
            case "gemini":
                headers["x-goog-api-key"] = credential.api_key.get_secret_value()
                # A fixed symmetric task keeps query and document vectors compatible.
                # The conservative byte budget is well below the model's input limit.
                body = orjson.dumps(
                    {
                        "requests": [
                            {
                                "model": f"models/{spec.model}",
                                "content": {"parts": [{"text": item.text}]},
                                "taskType": "SEMANTIC_SIMILARITY",
                                "outputDimensionality": spec.dimensions,
                            }
                            for item in request.items
                        ]
                    }
                )
                response = GeminiResponse.model_validate_json(
                    await self._post(spec.endpoint, headers, body)
                )
                tokens = (
                    response.usageMetadata.promptTokenCount
                    if response.usageMetadata
                    else None
                )
                vectors = [
                    ProviderVector(index=i, embedding=item.values)
                    for i, item in enumerate(response.embeddings)
                ]
            case "bedrock":
                # Titan accepts one input per request; the catalog enforces that bound.
                body = orjson.dumps(
                    {
                        "inputText": request.items[0].text,
                        "dimensions": spec.dimensions,
                        "normalize": True,
                    }
                )
                headers = await request_headers(
                    credential.values, request.scope, spec.endpoint, body
                )
                response = BedrockResponse.model_validate_json(
                    await self._post(spec.endpoint, headers, body)
                )
                tokens = response.inputTextTokenCount
                vectors = [ProviderVector(index=0, embedding=response.embedding)]
        parsed = ProviderResponse(
            model=spec.model,
            data=vectors,
            usage=ProviderUsage(prompt_tokens=tokens or 0, total_tokens=tokens or 0),
        )
        result = _validate_response(parsed, request, configuration)
        # Missing provider usage is unknown, not a claimed zero-token request.
        return EmbeddingBatch(result.results, tokens, tokens)
