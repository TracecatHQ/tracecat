"""One bounded provider call; no database, table, workflow or retry machinery."""

import asyncio
import hashlib
import math
import struct
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from tracecat.outbound import OutboundRequestDenied, create_outbound_http_client
from tracecat.search.embeddings.catalog import token_counter
from tracecat.search.embeddings.types import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingErrorCode,
    ModelSpec,
    PinnedConfiguration,
    ProviderResult,
    ResolvedCredential,
)
from tracecat.search.embeddings.wire import decode_response, encode_request
from tracecat.search.types import EmbeddingRequest, EmbeddingResult


def _check_token_budget(request: EmbeddingRequest, spec: ModelSpec) -> None:
    # Tokenization is CPU work. Run it off the event loop and stop as soon as a
    # budget is exceeded rather than processing the rest of an invalid batch.
    counter = token_counter(spec)
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
    response: ProviderResult,
    request: EmbeddingRequest,
) -> EmbeddingBatch:
    if len(response.vectors) != len(request.items):
        raise EmbeddingError(EmbeddingErrorCode.RESPONSE_INVALID)
    results = []
    for item, vector in zip(request.items, response.vectors, strict=True):
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
    return EmbeddingBatch(tuple(results), response.prompt_tokens, response.total_tokens)


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
                headers, body = await encode_request(spec, credential, request)
                raw = await self._post(
                    spec.endpoint,
                    headers,
                    body,
                    self_hosted=spec.provider in {"ollama", "vllm"},
                )
                return _validate_response(decode_response(spec, raw), request)

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
