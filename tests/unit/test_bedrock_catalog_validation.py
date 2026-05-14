"""Tests for Bedrock catalog target verification helpers."""

from __future__ import annotations

from typing import Any

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

    def fake_verify_with_boto3(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"runtime_test": "converse"}

    monkeypatch.setattr(
        bedrock_validation,
        "_verify_with_boto3",
        fake_verify_with_boto3,
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

    assert details == {"runtime_test": "converse"}
    assert captured["region"] == "us-east-1"
    assert captured["target_id"] == "us.anthropic.claude-sonnet-4"
    assert captured["is_inference_profile"] is True


@pytest.mark.anyio
async def test_verify_bedrock_catalog_target_uses_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_verify_with_api_key(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"runtime_test": "converse"}

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

    assert details == {"runtime_test": "converse"}
    assert captured["api_key"] == "bedrock-api-key"
    assert captured["target_id"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert captured["is_inference_profile"] is False


def test_check_profile_response_rejects_inactive_profile() -> None:
    with pytest.raises(BedrockVerificationError, match="CREATING"):
        bedrock_validation._check_profile_response({"status": "CREATING"})


def test_check_model_availability_response_rejects_unavailable_region() -> None:
    with pytest.raises(BedrockVerificationError, match="NOT_AVAILABLE"):
        bedrock_validation._check_model_availability_response(
            {
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "regionAvailability": "NOT_AVAILABLE",
            }
        )
