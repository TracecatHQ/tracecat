"""AWS Bedrock control-plane helpers for model discovery and subscriptions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict

import boto3
import botocore.session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.tokens import FrozenAuthToken

if TYPE_CHECKING:
    from types_boto3_bedrock.client import BedrockClient

BedrockSubscriptionStatus = Literal[
    "subscribed",
    "not_subscribed",
    "pending",
    "not_authorized",
    "unavailable",
    "error",
    "unknown",
]

BEDROCK_DISCOVERY_SOURCE = "bedrock"
BEDROCK_SUBSCRIPTION_METADATA_KEY = "bedrock_subscription"
BEDROCK_FOUNDATION_MODEL_METADATA_KEY = "foundation_model_id"
_DEFAULT_AWS_REGION = "us-east-1"
_DEFAULT_AWS_ROLE_SESSION_NAME = "tracecat-session"
_FOUNDATION_MODEL_ARN_MARKER = ":foundation-model/"
_AVAILABILITY_CONCURRENCY = 8
_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"mode": "standard", "total_max_attempts": 3},
)


class BedrockError(Exception):
    """A Bedrock control-plane call failed."""


class BedrockSubscription(TypedDict):
    status: BedrockSubscriptionStatus
    checked_at: str


@dataclass(frozen=True, slots=True)
class BedrockDiscoveredModel:
    """A Bedrock invocation target and the foundation model it resolves to."""

    model_name: str
    display_name: str
    foundation_model_id: str
    inference_profile_id: str | None = None
    model_id: str | None = None

    def to_metadata(self, subscription: BedrockSubscription) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "display_name": self.display_name,
            "discovery_source": BEDROCK_DISCOVERY_SOURCE,
            BEDROCK_FOUNDATION_MODEL_METADATA_KEY: self.foundation_model_id,
            BEDROCK_SUBSCRIPTION_METADATA_KEY: subscription,
        }
        if self.inference_profile_id is not None:
            metadata["inference_profile_id"] = self.inference_profile_id
        if self.model_id is not None:
            metadata["model_id"] = self.model_id
        return metadata


class _StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = FrozenAuthToken(token)

    def load_token(self, **_: object) -> FrozenAuthToken:
        return self._token


def _assume_role_session(
    role_arn: str,
    *,
    external_id: str,
    session_name: str,
    region: str,
) -> boto3.Session:
    # Ambient workload credentials are only the STS caller for the configured
    # role, matching the Bedrock runtime and embeddings trust model.
    sts = boto3.Session().client("sts", region_name=region, config=_CLIENT_CONFIG)
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        ExternalId=external_id,
    )
    credentials = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def create_bedrock_client(
    credentials: Mapping[str, str],
    *,
    external_ids: Iterable[str] = (),
) -> BedrockClient:
    """Create a Bedrock control-plane client from provider credentials.

    Uses the same precedence as the runtime gateway: ``AWS_ROLE_ARN``, then
    static keys, then ``AWS_BEARER_TOKEN_BEDROCK``. Role mode tries each
    candidate External ID until the role trust policy accepts one.
    """
    region = credentials.get("AWS_REGION") or _DEFAULT_AWS_REGION
    if role_arn := credentials.get("AWS_ROLE_ARN"):
        session_name = (
            credentials.get("AWS_ROLE_SESSION_NAME") or ""
        ).strip() or _DEFAULT_AWS_ROLE_SESSION_NAME
        last_error: Exception | None = None
        for external_id in external_ids:
            try:
                session = _assume_role_session(
                    role_arn,
                    external_id=external_id,
                    session_name=session_name,
                    region=region,
                )
            except (BotoCoreError, ClientError, KeyError) as exc:
                last_error = exc
                continue
            return session.client("bedrock", config=_CLIENT_CONFIG)
        raise BedrockError(
            "Failed to assume the configured AWS role for Bedrock. Ensure the "
            "role trust policy allows a workspace External ID."
        ) from last_error

    access_key = credentials.get("AWS_ACCESS_KEY_ID")
    secret_key = credentials.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=credentials.get("AWS_SESSION_TOKEN") or None,
            region_name=region,
        )
        return session.client("bedrock", config=_CLIENT_CONFIG)

    if token := credentials.get("AWS_BEARER_TOKEN_BEDROCK"):
        botocore_session = botocore.session.Session()
        botocore_session.register_component(
            "token_provider", _StaticTokenProvider(token)
        )
        session = boto3.Session(botocore_session=botocore_session, region_name=region)
        return session.client(
            "bedrock",
            config=_CLIENT_CONFIG.merge(Config(signature_version="bearer")),
        )

    raise BedrockError(
        "Bedrock requires one of AWS_ROLE_ARN, "
        "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or AWS_BEARER_TOKEN_BEDROCK."
    )


def _client_error_message(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code") or "ClientError"
        message = error.get("Message") or str(exc)
        return f"{code}: {message}"
    return str(exc)


def _foundation_model_id_from_arn(arn: str) -> str | None:
    _, marker, model_id = arn.partition(_FOUNDATION_MODEL_ARN_MARKER)
    return (model_id or None) if marker else None


def _is_chat_model(summary: Mapping[str, Any]) -> bool:
    lifecycle = summary.get("modelLifecycle") or {}
    return (
        "TEXT" in (summary.get("inputModalities") or [])
        and "TEXT" in (summary.get("outputModalities") or [])
        and bool(summary.get("responseStreamingSupported"))
        and lifecycle.get("status", "ACTIVE") == "ACTIVE"
    )


def _list_inference_profiles(client: BedrockClient) -> list[Mapping[str, Any]]:
    profiles: list[Mapping[str, Any]] = []
    next_token: str | None = None
    while True:
        response = (
            client.list_inference_profiles(nextToken=next_token)
            if next_token
            else client.list_inference_profiles()
        )
        profiles.extend(response.get("inferenceProfileSummaries", []))
        next_token = response.get("nextToken")
        if not next_token:
            return profiles


def list_bedrock_models(client: BedrockClient) -> list[BedrockDiscoveredModel]:
    """List text chat models invocable on demand or via inference profiles."""
    try:
        summaries = client.list_foundation_models(byOutputModality="TEXT").get(
            "modelSummaries", []
        )
        profiles = _list_inference_profiles(client)
    except (BotoCoreError, ClientError) as exc:
        raise BedrockError(
            f"Failed to list Bedrock models: {_client_error_message(exc)}"
        ) from exc

    chat_models = {
        summary["modelId"]: summary for summary in summaries if _is_chat_model(summary)
    }
    discovered: dict[str, BedrockDiscoveredModel] = {}
    for model_id, summary in chat_models.items():
        if "ON_DEMAND" not in (summary.get("inferenceTypesSupported") or []):
            continue
        discovered[model_id] = BedrockDiscoveredModel(
            model_name=model_id,
            display_name=summary.get("modelName") or model_id,
            foundation_model_id=model_id,
            model_id=model_id,
        )
    for profile in profiles:
        if profile.get("status", "ACTIVE") != "ACTIVE":
            continue
        foundation_ids = {
            fm_id
            for model in profile.get("models") or []
            if (fm_id := _foundation_model_id_from_arn(model.get("modelArn", "")))
        }
        # Multi-model application profiles have no single subscription target.
        if len(foundation_ids) != 1:
            continue
        foundation_id = next(iter(foundation_ids))
        if foundation_id not in chat_models:
            continue
        profile_id = profile["inferenceProfileId"]
        discovered[profile_id] = BedrockDiscoveredModel(
            model_name=profile_id,
            display_name=profile.get("inferenceProfileName") or profile_id,
            foundation_model_id=foundation_id,
            inference_profile_id=profile_id,
        )
    return sorted(discovered.values(), key=lambda model: model.model_name)


def subscription_status_from_availability(
    availability: Mapping[str, Any],
) -> BedrockSubscriptionStatus:
    """Collapse ``GetFoundationModelAvailability`` into one display status."""
    if availability.get("regionAvailability") == "NOT_AVAILABLE":
        return "unavailable"
    if availability.get("authorizationStatus") == "NOT_AUTHORIZED":
        return "not_authorized"
    agreement = (availability.get("agreementAvailability") or {}).get("status")
    match agreement:
        case "PENDING":
            return "pending"
        case "ERROR":
            return "error"
        case "NOT_AVAILABLE":
            return "not_subscribed"
        case "AVAILABLE":
            if availability.get("entitlementAvailability") == "NOT_AVAILABLE":
                return "not_subscribed"
            return "subscribed"
    return "unknown"


def _subscription(status: BedrockSubscriptionStatus) -> BedrockSubscription:
    return {"status": status, "checked_at": datetime.now(UTC).isoformat()}


def get_bedrock_subscription(
    client: BedrockClient, foundation_model_id: str
) -> BedrockSubscription:
    """Return the account's subscription status for one foundation model."""
    try:
        availability = client.get_foundation_model_availability(
            modelId=foundation_model_id
        )
    except (BotoCoreError, ClientError):
        return _subscription("unknown")
    return _subscription(subscription_status_from_availability(availability))


def get_bedrock_subscriptions(
    client: BedrockClient, foundation_model_ids: Iterable[str]
) -> dict[str, BedrockSubscription]:
    """Return subscription statuses keyed by foundation model ID."""
    unique_ids = sorted(set(foundation_model_ids))
    if not unique_ids:
        return {}
    with ThreadPoolExecutor(max_workers=_AVAILABILITY_CONCURRENCY) as pool:
        statuses = pool.map(
            lambda model_id: get_bedrock_subscription(client, model_id), unique_ids
        )
        return dict(zip(unique_ids, statuses, strict=True))


def subscribe_bedrock_model(
    client: BedrockClient, foundation_model_id: str
) -> BedrockSubscription:
    """Accept the model's public AWS Marketplace offer and return its status."""
    try:
        offers = client.list_foundation_model_agreement_offers(
            modelId=foundation_model_id, offerType="PUBLIC"
        ).get("offers", [])
        if not offers:
            raise BedrockError(
                f"No AWS Marketplace offer is available for {foundation_model_id}"
            )
        client.create_foundation_model_agreement(
            modelId=foundation_model_id, offerToken=offers[0]["offerToken"]
        )
    except (BotoCoreError, ClientError) as exc:
        raise BedrockError(
            f"Failed to subscribe to {foundation_model_id}: "
            f"{_client_error_message(exc)}"
        ) from exc
    subscription = get_bedrock_subscription(client, foundation_model_id)
    if subscription["status"] in {"not_subscribed", "unknown"}:
        # The agreement is accepted asynchronously (HTTP 202).
        return _subscription("pending")
    return subscription


def resolve_foundation_model_id(metadata: Mapping[str, Any]) -> str | None:
    """Resolve the foundation model behind a Bedrock catalog row's metadata."""
    for key in (BEDROCK_FOUNDATION_MODEL_METADATA_KEY, "model_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def resolve_profile_foundation_model_id(
    client: BedrockClient, inference_profile_id: str
) -> str | None:
    """Resolve a single-model inference profile to its foundation model ID."""
    try:
        profile = client.get_inference_profile(
            inferenceProfileIdentifier=inference_profile_id
        )
    except (BotoCoreError, ClientError) as exc:
        raise BedrockError(
            f"Failed to read inference profile {inference_profile_id}: "
            f"{_client_error_message(exc)}"
        ) from exc
    foundation_ids = {
        fm_id
        for model in profile.get("models") or []
        if (fm_id := _foundation_model_id_from_arn(model.get("modelArn", "")))
    }
    return next(iter(foundation_ids)) if len(foundation_ids) == 1 else None
