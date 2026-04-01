from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from google.protobuf.json_format import MessageToDict, ParseDict
from temporalio.api.common.v1 import Payloads

from tracecat import config
from tracecat.logger import logger
from tracecat.temporal.codec import decode_payloads

router = APIRouter(prefix="/codec", tags=["public"], include_in_schema=False)


def _verify_codec_auth(authorization: str | None = Header(default=None)) -> None:
    if not config.TEMPORAL__CODEC_SERVER_SHARED_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporal codec server is not configured",
        )
    expected = f"Bearer {config.TEMPORAL__CODEC_SERVER_SHARED_SECRET}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized codec request",
        )


@router.post("/decode", dependencies=[Depends(_verify_codec_auth)])
async def decode_codec_payloads(
    request: Request,
    x_namespace: str | None = Header(default=None, alias="X-Namespace"),
) -> dict[str, Any]:
    payloads = Payloads()
    ParseDict(await request.json(), payloads)
    decoded_payloads = await decode_payloads(
        payloads.payloads,
        compression_enabled=config.TRACECAT__CONTEXT_COMPRESSION_ENABLED,
    )
    logger.info(
        "Decoded Temporal codec payloads",
        namespace=x_namespace,
        payload_count=len(decoded_payloads),
    )
    return MessageToDict(
        Payloads(payloads=decoded_payloads),
        preserving_proto_field_name=True,
    )
