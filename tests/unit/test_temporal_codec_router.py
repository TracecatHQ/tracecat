from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.protobuf.json_format import MessageToDict
from temporalio.api.common.v1 import Payload, Payloads

from tracecat import config
from tracecat.contexts import with_temporal_workspace_id
from tracecat.temporal.codec import (
    get_payload_codec,
    reset_temporal_payload_secret_cache,
)
from tracecat.temporal.router import router


@pytest.fixture(autouse=True)
def reset_temporal_router_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN", None)
    monkeypatch.setattr(config, "TEMPORAL__CODEC_SERVER_SHARED_SECRET", None)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", False)
    reset_temporal_payload_secret_cache()


@pytest.mark.anyio
async def test_codec_router_decodes_encrypted_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )
    monkeypatch.setattr(config, "TEMPORAL__CODEC_SERVER_SHARED_SECRET", "codec-secret")

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None

    with with_temporal_workspace_id("workspace-123"):
        encoded_payloads = await codec.encode(
            [
                Payload(
                    metadata={"encoding": b"json/plain"},
                    data=b'{"secret":"sensitive"}',
                )
            ]
        )

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/codec/decode",
            json=MessageToDict(
                Payloads(payloads=encoded_payloads),
                preserving_proto_field_name=True,
            ),
            headers={
                "Authorization": "Bearer codec-secret",
                "X-Namespace": "tracecat-tests",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["payloads"][0]["metadata"]["encoding"] == "anNvbi9wbGFpbg=="
    assert body["payloads"][0]["data"] == "eyJzZWNyZXQiOiJzZW5zaXRpdmUifQ=="


def test_codec_router_rejects_unauthorized_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__CODEC_SERVER_SHARED_SECRET", "codec-secret")

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post("/codec/decode", json={"payloads": []})

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized codec request"
