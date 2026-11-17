"""Tests for the Bedrock control-plane helpers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from tracecat.agent.bedrock import (
    BedrockError,
    create_bedrock_client,
    get_bedrock_subscription,
    list_bedrock_models,
    resolve_foundation_model_id,
    resolve_profile_foundation_model_id,
    subscribe_bedrock_model,
    subscription_status_from_availability,
)

_REGION = "us-east-1"
_FM_ARN = f"arn:aws:bedrock:{_REGION}::foundation-model/"


@pytest.fixture
def client() -> Any:
    return boto3.client(
        "bedrock",
        region_name=_REGION,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


@pytest.fixture
def stubber(client: Any) -> Iterator[Stubber]:
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()


def _summary(
    model_id: str,
    *,
    inference_types: list[str],
    input_modalities: list[str] | None = None,
    streaming: bool = True,
    status: str = "ACTIVE",
) -> dict[str, Any]:
    return {
        "modelArn": f"{_FM_ARN}{model_id}",
        "modelId": model_id,
        "modelName": model_id.upper(),
        "providerName": "Synthetic",
        "inputModalities": input_modalities or ["TEXT"],
        "outputModalities": ["TEXT"],
        "responseStreamingSupported": streaming,
        "inferenceTypesSupported": inference_types,
        "modelLifecycle": {"status": status},
    }


def _profile(profile_id: str, *model_ids: str) -> dict[str, Any]:
    return {
        "inferenceProfileName": f"Profile {profile_id}",
        "inferenceProfileArn": f"arn:aws:bedrock:{_REGION}:123456789012:inference-profile/{profile_id}",
        "inferenceProfileId": profile_id,
        "status": "ACTIVE",
        "type": "SYSTEM_DEFINED",
        "models": [{"modelArn": f"{_FM_ARN}{model_id}"} for model_id in model_ids],
    }


def test_list_bedrock_models_includes_on_demand_and_single_model_profiles(
    client: Any, stubber: Stubber
) -> None:
    stubber.add_response(
        "list_foundation_models",
        {
            "modelSummaries": [
                _summary("vendor.on-demand-v1", inference_types=["ON_DEMAND"]),
                _summary(
                    "vendor.profile-only-v1", inference_types=["INFERENCE_PROFILE"]
                ),
                _summary(
                    "vendor.no-stream-v1",
                    inference_types=["ON_DEMAND"],
                    streaming=False,
                ),
                _summary(
                    "vendor.legacy-v1",
                    inference_types=["ON_DEMAND"],
                    status="LEGACY",
                ),
                _summary(
                    "vendor.image-in-v1",
                    inference_types=["ON_DEMAND"],
                    input_modalities=["IMAGE"],
                ),
            ]
        },
        {"byOutputModality": "TEXT"},
    )
    stubber.add_response(
        "list_inference_profiles",
        {
            "inferenceProfileSummaries": [
                _profile("us.vendor.profile-only-v1", "vendor.profile-only-v1")
            ],
            "nextToken": "page-2",
        },
        {},
    )
    stubber.add_response(
        "list_inference_profiles",
        {
            "inferenceProfileSummaries": [
                _profile(
                    "multi-model-app-profile",
                    "vendor.on-demand-v1",
                    "vendor.profile-only-v1",
                ),
                _profile("us.vendor.legacy-v1", "vendor.legacy-v1"),
            ]
        },
        {"nextToken": "page-2"},
    )

    models = list_bedrock_models(client)

    assert [(m.model_name, m.foundation_model_id) for m in models] == [
        ("us.vendor.profile-only-v1", "vendor.profile-only-v1"),
        ("vendor.on-demand-v1", "vendor.on-demand-v1"),
    ]
    profile_model, direct_model = models
    assert profile_model.inference_profile_id == "us.vendor.profile-only-v1"
    assert profile_model.model_id is None
    assert direct_model.model_id == "vendor.on-demand-v1"
    assert direct_model.inference_profile_id is None
    metadata = profile_model.to_metadata(
        {"status": "subscribed", "checked_at": "2026-01-01T00:00:00+00:00"}
    )
    assert metadata["inference_profile_id"] == "us.vendor.profile-only-v1"
    assert "model_id" not in metadata
    assert resolve_foundation_model_id(metadata) == "vendor.profile-only-v1"


def test_list_bedrock_models_wraps_client_errors(client: Any, stubber: Stubber) -> None:
    stubber.add_client_error(
        "list_foundation_models",
        service_error_code="AccessDeniedException",
        service_message="not allowed",
        http_status_code=403,
    )
    with pytest.raises(BedrockError, match="AccessDeniedException: not allowed"):
        list_bedrock_models(client)


@pytest.mark.parametrize(
    ("availability", "expected"),
    [
        (
            {
                "regionAvailability": "AVAILABLE",
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "agreementAvailability": {"status": "AVAILABLE"},
            },
            "subscribed",
        ),
        (
            {
                "regionAvailability": "AVAILABLE",
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "NOT_AVAILABLE",
                "agreementAvailability": {"status": "NOT_AVAILABLE"},
            },
            "not_subscribed",
        ),
        (
            {
                "regionAvailability": "AVAILABLE",
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "NOT_AVAILABLE",
                "agreementAvailability": {"status": "PENDING"},
            },
            "pending",
        ),
        (
            {
                "regionAvailability": "AVAILABLE",
                "authorizationStatus": "NOT_AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "agreementAvailability": {"status": "AVAILABLE"},
            },
            "not_authorized",
        ),
        (
            {
                "regionAvailability": "NOT_AVAILABLE",
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "agreementAvailability": {"status": "AVAILABLE"},
            },
            "unavailable",
        ),
        (
            {
                "regionAvailability": "AVAILABLE",
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "agreementAvailability": {"status": "ERROR"},
            },
            "error",
        ),
        ({}, "unknown"),
    ],
)
def test_subscription_status_from_availability(
    availability: dict[str, Any], expected: str
) -> None:
    assert subscription_status_from_availability(availability) == expected


def _offer() -> dict[str, Any]:
    return {
        "offerToken": "offer-token",
        "termDetails": {
            "usageBasedPricingTerm": {"rateCard": []},
            "legalTerm": {"url": "https://example.invalid/eula"},
            "supportTerm": {"refundPolicyDescription": "none"},
        },
    }


def _availability(agreement_status: str, entitlement: str) -> dict[str, Any]:
    return {
        "modelId": "vendor.model-v1",
        "agreementAvailability": {"status": agreement_status},
        "authorizationStatus": "AUTHORIZED",
        "entitlementAvailability": entitlement,
        "regionAvailability": "AVAILABLE",
    }


def test_get_bedrock_subscription_reports_unknown_on_error(
    client: Any, stubber: Stubber
) -> None:
    stubber.add_client_error(
        "get_foundation_model_availability",
        service_error_code="AccessDeniedException",
        http_status_code=403,
    )
    assert get_bedrock_subscription(client, "vendor.model-v1")["status"] == "unknown"


def test_subscribe_bedrock_model_accepts_public_offer_and_reports_pending(
    client: Any, stubber: Stubber
) -> None:
    stubber.add_response(
        "list_foundation_model_agreement_offers",
        {
            "modelId": "vendor.model-v1",
            "offers": [_offer()],
        },
        {"modelId": "vendor.model-v1", "offerType": "PUBLIC"},
    )
    stubber.add_response(
        "create_foundation_model_agreement",
        {"modelId": "vendor.model-v1"},
        {"modelId": "vendor.model-v1", "offerToken": "offer-token"},
    )
    stubber.add_response(
        "get_foundation_model_availability",
        _availability("NOT_AVAILABLE", "NOT_AVAILABLE"),
        {"modelId": "vendor.model-v1"},
    )

    assert subscribe_bedrock_model(client, "vendor.model-v1")["status"] == "pending"


def test_subscribe_bedrock_model_reports_subscribed_when_active(
    client: Any, stubber: Stubber
) -> None:
    stubber.add_response(
        "list_foundation_model_agreement_offers",
        {
            "modelId": "vendor.model-v1",
            "offers": [_offer()],
        },
    )
    stubber.add_response(
        "create_foundation_model_agreement", {"modelId": "vendor.model-v1"}
    )
    stubber.add_response(
        "get_foundation_model_availability",
        _availability("AVAILABLE", "AVAILABLE"),
    )

    assert subscribe_bedrock_model(client, "vendor.model-v1")["status"] == "subscribed"


def test_subscribe_bedrock_model_requires_an_offer(
    client: Any, stubber: Stubber
) -> None:
    stubber.add_response(
        "list_foundation_model_agreement_offers",
        {"modelId": "vendor.model-v1", "offers": []},
    )
    with pytest.raises(BedrockError, match="No AWS Marketplace offer"):
        subscribe_bedrock_model(client, "vendor.model-v1")


def test_resolve_profile_foundation_model_id(client: Any, stubber: Stubber) -> None:
    profile = _profile("us.vendor.model-v1", "vendor.model-v1")
    stubber.add_response(
        "get_inference_profile",
        {**profile, "inferenceProfileArn": profile["inferenceProfileArn"]},
        {"inferenceProfileIdentifier": "us.vendor.model-v1"},
    )
    assert (
        resolve_profile_foundation_model_id(client, "us.vendor.model-v1")
        == "vendor.model-v1"
    )


def test_create_bedrock_client_requires_credentials() -> None:
    with pytest.raises(BedrockError, match="Bedrock requires one of"):
        create_bedrock_client({"AWS_REGION": _REGION})


def test_create_bedrock_client_role_mode_requires_accepted_external_id() -> None:
    with pytest.raises(BedrockError, match="Failed to assume"):
        create_bedrock_client(
            {"AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/synthetic"},
            external_ids=[],
        )


def test_create_bedrock_client_bearer_token_signs_with_bearer_auth() -> None:
    client = create_bedrock_client(
        {"AWS_BEARER_TOKEN_BEDROCK": "synthetic-token", "AWS_REGION": _REGION}
    )
    captured: dict[str, str] = {}

    def _capture(request: Any, **_: Any) -> None:
        captured["authorization"] = request.headers["Authorization"]
        raise EndpointConnectionError(endpoint_url="https://synthetic.invalid")

    client.meta.events.register("before-send.bedrock.*", _capture)
    with pytest.raises(BedrockError):
        list_bedrock_models(client)
    assert captured["authorization"] in {
        "Bearer synthetic-token",
        b"Bearer synthetic-token",
    }
