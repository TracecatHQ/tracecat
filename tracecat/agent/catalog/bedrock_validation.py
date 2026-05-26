"""Bedrock catalog target verification helpers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import cast

import aioboto3
import aiobotocore.session
import orjson
from aiobotocore.config import AioConfig
from botocore.exceptions import BotoCoreError, ClientError
from botocore.tokens import FrozenAuthToken

from tracecat.agent.catalog.types import (
    AnthropicInvokePayload,
    BedrockControlClient,
    BedrockInferenceConfig,
    BedrockInferenceProfileDetails,
    BedrockInferenceProfileResponse,
    BedrockMessage,
    BedrockModelAvailabilityDetails,
    BedrockModelAvailabilityResponse,
    BedrockRuntimeClient,
    BedrockVerificationDetails,
)

_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY = "TRACECAT_AWS_EXTERNAL_ID"
_DEFAULT_AWS_ROLE_SESSION_NAME = "tracecat-session"
_BEDROCK_BEARER_CONFIG = AioConfig(signature_version="bearer")


class StaticBearerTokenProvider:
    """Botocore token provider for Bedrock bearer-token SDK auth."""

    def __init__(self, token: str) -> None:
        self._token = token

    def load_token(self, **_: object) -> FrozenAuthToken:
        return FrozenAuthToken(self._token)


_PING_MESSAGES: list[BedrockMessage] = [
    {"role": "user", "content": [{"text": "ping"}]},
]
_PING_INFERENCE_CONFIG: BedrockInferenceConfig = {"maxTokens": 1}
_ANTHROPIC_INVOKE_PING: AnthropicInvokePayload = {
    "anthropic_version": "bedrock-2023-05-31",
    "max_tokens": 1,
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "ping"}]},
    ],
}


class BedrockVerificationError(Exception):
    """Raised when Bedrock target verification fails."""


async def _assume_bedrock_role(credentials: dict[str, str]) -> dict[str, str]:
    role_arn = credentials["AWS_ROLE_ARN"]
    external_id = credentials.get(_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY)
    if not external_id:
        raise BedrockVerificationError(
            "Bedrock role credentials require a Tracecat-provided AWS External ID."
        )
    session_name = (
        credentials.get("AWS_ROLE_SESSION_NAME", "").strip()
        or _DEFAULT_AWS_ROLE_SESSION_NAME
    )
    session = aioboto3.Session()
    async with session.client("sts") as client:
        response = await client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            ExternalId=external_id,
        )
    session_credentials = response["Credentials"]
    return credentials | {
        "AWS_ACCESS_KEY_ID": session_credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": session_credentials["SecretAccessKey"],
        "AWS_SESSION_TOKEN": session_credentials["SessionToken"],
    }


async def _resolve_bedrock_credentials(
    credentials: dict[str, str],
) -> dict[str, str]:
    """Resolve configured Bedrock credentials into an explicit auth shape."""
    if credentials.get("AWS_ROLE_ARN"):
        try:
            return await _assume_bedrock_role(credentials)
        except BedrockVerificationError:
            raise
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise BedrockVerificationError(
                "Failed to assume configured AWS role for Bedrock."
            ) from exc

    access_key = credentials.get("AWS_ACCESS_KEY_ID")
    secret_key = credentials.get("AWS_SECRET_ACCESS_KEY")
    session_token = credentials.get("AWS_SESSION_TOKEN")
    if access_key and secret_key:
        return credentials
    if access_key or secret_key or session_token:
        raise BedrockVerificationError(
            "Bedrock static credentials require AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )

    if credentials.get("AWS_BEARER_TOKEN_BEDROCK"):
        return credentials

    raise BedrockVerificationError(
        "Bedrock requires AWS_ROLE_ARN, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or AWS_BEARER_TOKEN_BEDROCK."
    )


def _check_profile_response(
    response: BedrockInferenceProfileResponse,
) -> BedrockInferenceProfileDetails:
    status = response.get("status")
    details: BedrockInferenceProfileDetails = {
        "target_type": "inference_profile",
        "model_count": len(response.get("models") or []),
    }
    if status is not None:
        details["status"] = status
    if (inference_profile_id := response.get("inferenceProfileId")) is not None:
        details["inference_profile_id"] = inference_profile_id
    if (inference_profile_arn := response.get("inferenceProfileArn")) is not None:
        details["inference_profile_arn"] = inference_profile_arn
    if status and status != "ACTIVE":
        raise BedrockVerificationError(
            f"Inference profile is {status}; expected ACTIVE."
        )
    return details


def _check_model_availability_response(
    response: BedrockModelAvailabilityResponse,
) -> BedrockModelAvailabilityDetails:
    authorization_status = response.get("authorizationStatus")
    entitlement_availability = response.get("entitlementAvailability")
    region_availability = response.get("regionAvailability")
    details: BedrockModelAvailabilityDetails = {
        "target_type": "model_id",
    }
    if authorization_status is not None:
        details["authorization_status"] = authorization_status
    if entitlement_availability is not None:
        details["entitlement_availability"] = entitlement_availability
    if region_availability is not None:
        details["region_availability"] = region_availability
    return details


def _build_static_bearer_session(api_key: str, region: str) -> aioboto3.Session:
    botocore_session = aiobotocore.session.get_session()
    botocore_session.register_component(
        "token_provider",
        StaticBearerTokenProvider(api_key),
    )
    return aioboto3.Session(botocore_session=botocore_session, region_name=region)


def _client_context(
    session: aioboto3.Session,
    service_name: str,
    *,
    region: str,
    config: AioConfig | None = None,
) -> AbstractAsyncContextManager[object]:
    client = cast(Callable[..., AbstractAsyncContextManager[object]], session.client)
    return client(service_name, region_name=region, config=config)


def _is_anthropic_claude_target(target_id: str) -> bool:
    return "anthropic.claude" in target_id.lower()


async def _verify_with_invoke_model(
    runtime_client: BedrockRuntimeClient,
    target_id: str,
) -> None:
    if not _is_anthropic_claude_target(target_id):
        raise BedrockVerificationError(
            "InvokeModel runtime verification is currently supported for Anthropic Claude Bedrock targets only. Enable Use Converse API or use an Anthropic Claude model ID/inference profile."
        )
    await runtime_client.invoke_model(
        modelId=target_id,
        body=orjson.dumps(_ANTHROPIC_INVOKE_PING),
        contentType="application/json",
        accept="application/json",
    )


async def _verify_with_sdk_clients(
    *,
    session: aioboto3.Session,
    region: str,
    target_id: str,
    is_inference_profile: bool,
    use_converse: bool,
    client_config: AioConfig | None = None,
) -> BedrockVerificationDetails:
    async with _client_context(
        session,
        "bedrock",
        region=region,
        config=client_config,
    ) as bedrock:
        bedrock_client = cast(BedrockControlClient, bedrock)
        if is_inference_profile:
            details = _check_profile_response(
                await bedrock_client.get_inference_profile(
                    inferenceProfileIdentifier=target_id,
                )
            )
        else:
            details = _check_model_availability_response(
                await bedrock_client.get_foundation_model_availability(
                    modelId=target_id,
                )
            )

    async with _client_context(
        session,
        "bedrock-runtime",
        region=region,
        config=client_config,
    ) as runtime:
        runtime_client = cast(BedrockRuntimeClient, runtime)
        if use_converse:
            await runtime_client.converse(
                modelId=target_id,
                messages=_PING_MESSAGES,
                inferenceConfig=_PING_INFERENCE_CONFIG,
            )
        else:
            await _verify_with_invoke_model(runtime_client, target_id)
    return details


async def _verify_with_aws_credentials(
    *,
    credentials: dict[str, str],
    region: str,
    target_id: str,
    is_inference_profile: bool,
    use_converse: bool,
) -> BedrockVerificationDetails:
    session = aioboto3.Session(
        aws_access_key_id=credentials["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=credentials["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=credentials.get("AWS_SESSION_TOKEN"),
        region_name=region,
    )
    return await _verify_with_sdk_clients(
        session=session,
        region=region,
        target_id=target_id,
        is_inference_profile=is_inference_profile,
        use_converse=use_converse,
    )


async def _verify_with_api_key(
    *,
    api_key: str,
    region: str,
    target_id: str,
    is_inference_profile: bool,
    use_converse: bool,
) -> BedrockVerificationDetails:
    session = _build_static_bearer_session(api_key, region)
    return await _verify_with_sdk_clients(
        session=session,
        region=region,
        target_id=target_id,
        is_inference_profile=is_inference_profile,
        use_converse=use_converse,
        client_config=_BEDROCK_BEARER_CONFIG,
    )


async def verify_bedrock_catalog_target(
    *,
    credentials: dict[str, str],
    inference_profile_id: str | None,
    model_id: str | None,
    use_converse: bool,
) -> BedrockVerificationDetails:
    """Verify a Bedrock catalog target with control-plane and runtime calls."""
    region = credentials.get("AWS_REGION")
    if not region:
        raise BedrockVerificationError(
            "AWS_REGION is required to verify Bedrock models."
        )
    target_id = inference_profile_id or model_id
    if target_id is None:
        raise BedrockVerificationError(
            "Provide exactly one of inference_profile_id or model_id."
        )

    resolved = await _resolve_bedrock_credentials(credentials)
    is_inference_profile = inference_profile_id is not None
    try:
        has_aws_credentials = bool(
            resolved.get("AWS_ACCESS_KEY_ID") and resolved.get("AWS_SECRET_ACCESS_KEY")
        )
        if not has_aws_credentials and (
            api_key := resolved.get("AWS_BEARER_TOKEN_BEDROCK")
        ):
            return await _verify_with_api_key(
                api_key=api_key,
                region=region,
                target_id=target_id,
                is_inference_profile=is_inference_profile,
                use_converse=use_converse,
            )

        return await _verify_with_aws_credentials(
            credentials=resolved,
            region=region,
            target_id=target_id,
            is_inference_profile=is_inference_profile,
            use_converse=use_converse,
        )
    except BedrockVerificationError:
        raise
    except ClientError as exc:
        error = exc.response.get("Error", {})
        code = error.get("Code")
        message = error.get("Message")
        if code and message:
            raise BedrockVerificationError(f"{code}: {message}") from exc
        raise BedrockVerificationError(str(exc)) from exc
    except BotoCoreError as exc:
        raise BedrockVerificationError(str(exc)) from exc
    except Exception as exc:
        raise BedrockVerificationError(str(exc)) from exc
