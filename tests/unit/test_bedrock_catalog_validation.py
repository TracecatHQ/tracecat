"""Tests for Bedrock catalog target verification helpers."""

from __future__ import annotations

from typing import Any, cast

import orjson
import pytest

from tracecat.agent.catalog import bedrock_validation
from tracecat.agent.catalog.bedrock_validation import (
    BedrockVerificationError,
    verify_bedrock_catalog_target,
)


@pytest.mark.anyio
async def test_verify_bedrock_catalog_target_requires_region() -> None:
    with pytest.raises(BedrockVerificationError, match="AWS_REGION"):
        await verify_bedrock_catalog_target(
            credentials={
                "AWS_ACCESS_KEY_ID": "access-key",
                "AWS_SECRET_ACCESS_KEY": "secret-key",
            },
            inference_profile_id="us.anthropic.claude-sonnet-4",
            model_id=None,
            use_converse=True,
        )


@pytest.mark.anyio
async def test_verify_bedrock_catalog_target_requires_external_id_for_role() -> None:
    with pytest.raises(BedrockVerificationError, match="AWS External ID"):
        await verify_bedrock_catalog_target(
            credentials={
                "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
                "AWS_REGION": "us-east-1",
            },
            inference_profile_id="us.anthropic.claude-sonnet-4",
            model_id=None,
            use_converse=True,
        )


@pytest.mark.anyio
async def test_verify_bedrock_catalog_target_uses_static_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_verify_with_aws_credentials(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"target_type": "inference_profile", "model_count": 1}

    monkeypatch.setattr(
        bedrock_validation,
        "_verify_with_aws_credentials",
        fake_verify_with_aws_credentials,
    )

    details = await verify_bedrock_catalog_target(
        credentials={
            "AWS_ACCESS_KEY_ID": "access-key",
            "AWS_SECRET_ACCESS_KEY": "secret-key",
            "AWS_REGION": "us-east-1",
        },
        inference_profile_id="us.anthropic.claude-sonnet-4",
        model_id=None,
        use_converse=True,
    )

    assert details == {"target_type": "inference_profile", "model_count": 1}
    assert captured["region"] == "us-east-1"
    assert captured["target_id"] == "us.anthropic.claude-sonnet-4"
    assert captured["is_inference_profile"] is True


@pytest.mark.anyio
async def test_verify_bedrock_catalog_target_preserves_static_auth_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_verify_with_aws_credentials(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"target_type": "model_id"}

    async def fake_verify_with_api_key(**_: Any) -> dict[str, Any]:
        pytest.fail("Bearer token auth should not be used when static keys are set")

    monkeypatch.setattr(
        bedrock_validation,
        "_verify_with_aws_credentials",
        fake_verify_with_aws_credentials,
    )
    monkeypatch.setattr(
        bedrock_validation,
        "_verify_with_api_key",
        fake_verify_with_api_key,
    )

    details = await verify_bedrock_catalog_target(
        credentials={
            "AWS_ACCESS_KEY_ID": "access-key",
            "AWS_SECRET_ACCESS_KEY": "secret-key",
            "AWS_BEARER_TOKEN_BEDROCK": "stale-bedrock-api-key",
            "AWS_REGION": "us-east-1",
        },
        inference_profile_id=None,
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        use_converse=True,
    )

    assert details == {"target_type": "model_id"}
    assert captured["credentials"]["AWS_ACCESS_KEY_ID"] == "access-key"
    assert captured["credentials"]["AWS_BEARER_TOKEN_BEDROCK"] == (
        "stale-bedrock-api-key"
    )


@pytest.mark.anyio
async def test_verify_bedrock_catalog_target_uses_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_verify_with_api_key(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"target_type": "model_id"}

    monkeypatch.setattr(
        bedrock_validation,
        "_verify_with_api_key",
        fake_verify_with_api_key,
    )

    details = await verify_bedrock_catalog_target(
        credentials={
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-api-key",
            "AWS_REGION": "us-east-1",
        },
        inference_profile_id=None,
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        use_converse=True,
    )

    assert details == {"target_type": "model_id"}
    assert captured["api_key"] == "bedrock-api-key"
    assert captured["target_id"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert captured["is_inference_profile"] is False


@pytest.mark.anyio
async def test_verify_with_sdk_clients_uses_invoke_model_when_not_converse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    captured_invoke: dict[str, Any] = {}

    class FakeBedrockControlClient:
        async def get_foundation_model_availability(
            self,
            *,
            modelId: str,
        ) -> dict[str, str]:
            return {
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "regionAvailability": "AVAILABLE",
            }

    class FakeBedrockRuntimeClient:
        async def invoke_model(self, **kwargs: Any) -> object:
            captured_invoke.update(kwargs)
            return {}

    class FakeClientContext:
        def __init__(self, client: object) -> None:
            self.client = client

        async def __aenter__(self) -> object:
            return self.client

        async def __aexit__(self, *_: object) -> None:
            return None

    def fake_client_context(
        _session: object,
        service_name: str,
        **_: object,
    ) -> FakeClientContext:
        calls.append(service_name)
        if service_name == "bedrock":
            return FakeClientContext(FakeBedrockControlClient())
        if service_name == "bedrock-runtime":
            return FakeClientContext(FakeBedrockRuntimeClient())
        pytest.fail(f"Unexpected service {service_name}")

    monkeypatch.setattr(bedrock_validation, "_client_context", fake_client_context)

    details = await bedrock_validation._verify_with_sdk_clients(
        session=cast(Any, object()),
        region="us-east-1",
        target_id="anthropic.claude-3-haiku-20240307-v1:0",
        is_inference_profile=False,
        use_converse=False,
    )

    assert details == {
        "target_type": "model_id",
        "authorization_status": "AUTHORIZED",
        "entitlement_availability": "AVAILABLE",
        "region_availability": "AVAILABLE",
    }
    assert calls == ["bedrock", "bedrock-runtime"]
    assert captured_invoke["modelId"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert captured_invoke["contentType"] == "application/json"
    assert captured_invoke["accept"] == "application/json"
    assert orjson.loads(captured_invoke["body"]) == {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
    }


@pytest.mark.anyio
async def test_verify_with_sdk_clients_rejects_unknown_invoke_payload_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBedrockControlClient:
        async def get_foundation_model_availability(
            self,
            *,
            modelId: str,
        ) -> dict[str, str]:
            return {
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "regionAvailability": "AVAILABLE",
            }

    class FakeBedrockRuntimeClient:
        async def invoke_model(self, **_: Any) -> object:
            pytest.fail("Unsupported invoke payload family should fail before invoke")

    class FakeClientContext:
        def __init__(self, client: object) -> None:
            self.client = client

        async def __aenter__(self) -> object:
            return self.client

        async def __aexit__(self, *_: object) -> None:
            return None

    def fake_client_context(
        _session: object,
        service_name: str,
        **_: object,
    ) -> FakeClientContext:
        if service_name == "bedrock":
            return FakeClientContext(FakeBedrockControlClient())
        if service_name == "bedrock-runtime":
            return FakeClientContext(FakeBedrockRuntimeClient())
        pytest.fail(f"Unexpected service {service_name}")

    monkeypatch.setattr(bedrock_validation, "_client_context", fake_client_context)

    with pytest.raises(BedrockVerificationError, match="Anthropic Claude"):
        await bedrock_validation._verify_with_sdk_clients(
            session=cast(Any, object()),
            region="us-east-1",
            target_id="amazon.titan-text-lite-v1",
            is_inference_profile=False,
            use_converse=False,
        )


def test_check_profile_response_rejects_inactive_profile() -> None:
    with pytest.raises(BedrockVerificationError, match="CREATING"):
        bedrock_validation._check_profile_response({"status": "CREATING"})


def test_check_model_availability_response_returns_status_details() -> None:
    assert bedrock_validation._check_model_availability_response(
        {
            "authorizationStatus": "AUTHORIZED",
            "entitlementAvailability": "AVAILABLE",
            "regionAvailability": "NOT_AVAILABLE",
        }
    ) == {
        "target_type": "model_id",
        "authorization_status": "AUTHORIZED",
        "entitlement_availability": "AVAILABLE",
        "region_availability": "NOT_AVAILABLE",
    }
