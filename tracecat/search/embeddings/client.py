"""One bounded provider call; no database, table, workflow or retry machinery."""

import asyncio
import hashlib
import math
import struct
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from tracecat.search.embeddings.catalog import EmbeddingTokenCounter
from tracecat.search.embeddings.types import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingErrorCode,
    ModelSpec,
    PinnedConfiguration,
    ProviderResponse,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingRequest, EmbeddingResult


def _check_token_budget(request: EmbeddingRequest, spec: ModelSpec) -> None:
    # Tokenization is CPU work. Run it off the event loop and stop as soon as a
    # budget is exceeded rather than processing the rest of an invalid batch.
    counter = EmbeddingTokenCounter()
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
    """Use a caller-owned HTTP client, with no redirects or implicit retries."""

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
        try:
            async with asyncio.timeout(self.timeout):
                await asyncio.to_thread(_check_token_budget, request, spec)
                async with self.http.stream(
                    "POST",
                    spec.endpoint,
                    headers={
                        "Authorization": f"Bearer {credential.api_key.get_secret_value()}"
                    },
                    json={
                        "model": spec.model,
                        "input": [item.text for item in request.items],
                        "encoding_format": "float",
                    },
                    timeout=self.timeout,
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise _status_error(response)
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 4_000_000:
                            raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
                parsed = ProviderResponse.model_validate_json(body)
                return _validate_response(parsed, request, configuration)
        except EmbeddingError as exc:
            error = EmbeddingError(exc.code, exc.retry_after)
        except (TimeoutError, httpx.TimeoutException):
            error = EmbeddingError(EmbeddingErrorCode.TIMEOUT)
        except (ValidationError, OverflowError, struct.error):
            error = EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
        except Exception:
            # Includes transport and tokenizer initialization failures. Never retain
            # their messages: either can carry request data or credential values.
            error = EmbeddingError(EmbeddingErrorCode.UNAVAILABLE)
        raise error
