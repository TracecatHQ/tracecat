"""Public ingest route for SNS-backed event sources.

No user auth, like incoming webhooks: the URL token finds the source, and the
SNS signature plus the pinned topic decide whether the message is accepted.

SNS retries a delivery only on a 5xx or a timeout. Answers:

- 200 once every event is committed to the outbox (or there was nothing to
  queue: a test event, a duplicate, a confirmation).
- 4xx when retrying cannot help: SNS drops the message, or moves it to the
  subscription's dead-letter queue.
- 503 when a retry can help: over the rate cap (with ``Retry-After``), the
  certificate host or the SNS API unreachable. A failed outbox write
  surfaces as a 500, which SNS also retries.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import ORJSONResponse
from starlette.requests import ClientDisconnect

from tracecat.logger import logger
from tracecat.triggers.enums import EventSourceKind
from tracecat.triggers.ingest.service import handle_sns_message
from tracecat.triggers.ingest.types import IngestError, IngestErrorCode
from tracecat.triggers.types import INGEST_PATH_BY_SOURCE_KIND

# SNS messages are at most 256 KB. The envelope JSON-escapes the message and
# adds the signature and URLs, so allow twice that.
MAX_BODY_BYTES = 512 * 1024
# Tokens are secrets.token_urlsafe(32): 43 URL-safe characters.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")

_STATUS_BY_CODE: dict[IngestErrorCode, int] = {
    IngestErrorCode.PAYLOAD_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    IngestErrorCode.MALFORMED: status.HTTP_400_BAD_REQUEST,
    IngestErrorCode.RAW_DELIVERY: status.HTTP_400_BAD_REQUEST,
    IngestErrorCode.SOURCE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    IngestErrorCode.NOT_ENTITLED: status.HTTP_403_FORBIDDEN,
    IngestErrorCode.TOPIC_MISMATCH: status.HTTP_403_FORBIDDEN,
    IngestErrorCode.SIGNATURE_INVALID: status.HTTP_403_FORBIDDEN,
    IngestErrorCode.STALE_MESSAGE: status.HTTP_400_BAD_REQUEST,
    IngestErrorCode.SOURCE_OFFLINE: status.HTTP_409_CONFLICT,
    IngestErrorCode.CONFIRMATION_REJECTED: status.HTTP_400_BAD_REQUEST,
    IngestErrorCode.RATE_LIMITED: status.HTTP_503_SERVICE_UNAVAILABLE,
    IngestErrorCode.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}

# Machine-to-machine; left out of the OpenAPI schema and the generated client.
router = APIRouter(
    prefix=INGEST_PATH_BY_SOURCE_KIND[EventSourceKind.AWS_SNS],
    tags=["triggers"],
    include_in_schema=False,
)


def _error_response(error: IngestError) -> Response:
    headers: dict[str, str] = {}
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return ORJSONResponse(
        status_code=_STATUS_BY_CODE[error.code],
        content={"detail": {"code": error.code.value, "message": str(error)}},
        headers=headers,
    )


async def _read_capped_body(request: Request) -> bytes:
    """Read the body, refusing it as soon as it passes ``MAX_BODY_BYTES``.

    Raises:
        IngestError: ``PAYLOAD_TOO_LARGE`` or ``MALFORMED`` (client left).
    """
    too_large = IngestError(
        IngestErrorCode.PAYLOAD_TOO_LARGE, "Body is larger than an SNS message"
    )
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_large
    body = bytearray()
    disconnected = False
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                raise too_large
    except ClientDisconnect:
        disconnected = True
    if disconnected:
        # Raised outside the handler so the disconnect is not kept as context.
        raise IngestError(IngestErrorCode.MALFORMED, "Client disconnected")
    return bytes(body)


@router.post("/{endpoint_token}")
async def ingest_sns_message(
    endpoint_token: str,
    request: Request,
    raw_delivery: Annotated[str | None, Header(alias="x-amz-sns-rawdelivery")] = None,
    message_type: Annotated[str | None, Header(alias="x-amz-sns-message-type")] = None,
) -> Response:
    """Receive one SNS message for the event source behind ``endpoint_token``."""
    received_at = datetime.now(UTC)
    try:
        if not _TOKEN_PATTERN.fullmatch(endpoint_token):
            raise IngestError(IngestErrorCode.SOURCE_NOT_FOUND, "Not found")
        body = await _read_capped_body(request)
        result = await handle_sns_message(
            endpoint_token=endpoint_token,
            body=body,
            raw_delivery=raw_delivery,
            message_type=message_type,
            received_at=received_at,
        )
    except IngestError as e:
        logger.info("SNS message refused", code=e.code.value, reason=str(e))
        return _error_response(e)
    return ORJSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "queued": result.queued,
            "duplicates": result.duplicates,
            "ignored": result.ignored,
            "confirmed": result.confirmed,
        },
    )
