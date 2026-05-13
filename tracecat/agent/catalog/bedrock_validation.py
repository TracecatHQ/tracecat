"""Bedrock catalog target verification helpers."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError

_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY = "TRACECAT_AWS_EXTERNAL_ID"
_DEFAULT_AWS_ROLE_SESSION_NAME = "tracecat-session"
_PING_MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": [{"text": "ping"}]},
]
_PING_INFERENCE_CONFIG = {"maxTokens": 1}


class BedrockVerificationError(Exception):
    """Raised when Bedrock target verification fails."""


def _get_required_region(credentials: dict[str, str]) -> str:
    if region := credentials.get("AWS_REGION"):
        return region
    raise BedrockVerificationError("AWS_REGION is required to verify Bedrock models.")


def _get_aws_role_session_name(credentials: dict[str, str]) -> str:
    if session_name := credentials.get("AWS_ROLE_SESSION_NAME"):
        if session_name := session_name.strip():
            return session_name
    return _DEFAULT_AWS_ROLE_SESSION_NAME


def _assume_bedrock_role(credentials: dict[str, str]) -> dict[str, str]:
    role_arn = credentials["AWS_ROLE_ARN"]
    external_id = credentials.get(_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY)
    if not external_id:
        raise BedrockVerificationError(
            "Bedrock role credentials require a workspace to build the AWS External ID."
        )

    sts_client = boto3.Session().client("sts")
    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=_get_aws_role_session_name(credentials),
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
            return await asyncio.to_thread(_assume_bedrock_role, credentials)
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


def _boto3_session(credentials: dict[str, str], region: str) -> boto3.Session:
    return boto3.Session(
        aws_access_key_id=credentials["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=credentials["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=credentials.get("AWS_SESSION_TOKEN"),
        region_name=region,
    )


def _check_profile_response(response: dict[str, Any]) -> dict[str, Any]:
    status = response.get("status")
    details = {
        "target_type": "inference_profile",
        "status": status,
        "inference_profile_id": response.get("inferenceProfileId"),
        "inference_profile_arn": response.get("inferenceProfileArn"),
        "model_count": len(response.get("models") or []),
    }
    if status and status != "ACTIVE":
        raise BedrockVerificationError(
            f"Inference profile is {status}; expected ACTIVE."
        )
    return {key: value for key, value in details.items() if value is not None}


def _check_model_availability_response(response: dict[str, Any]) -> dict[str, Any]:
    authorization_status = response.get("authorizationStatus")
    entitlement_availability = response.get("entitlementAvailability")
    region_availability = response.get("regionAvailability")
    details = {
        "target_type": "model_id",
        "authorization_status": authorization_status,
        "entitlement_availability": entitlement_availability,
        "region_availability": region_availability,
    }
    failures: list[str] = []
    if authorization_status and authorization_status != "AUTHORIZED":
        failures.append(f"authorization status is {authorization_status}")
    if entitlement_availability and entitlement_availability != "AVAILABLE":
        failures.append(f"entitlement availability is {entitlement_availability}")
    if region_availability and region_availability != "AVAILABLE":
        failures.append(f"region availability is {region_availability}")
    if failures:
        raise BedrockVerificationError(
            "Foundation model is not available: " + ", ".join(failures) + "."
        )
    return {key: value for key, value in details.items() if value is not None}


def _verify_with_boto3(
    *,
    credentials: dict[str, str],
    region: str,
    target_id: str,
    is_inference_profile: bool,
    use_converse: bool,
) -> dict[str, Any]:
    session = _boto3_session(credentials, region)
    bedrock = session.client("bedrock", region_name=region)
    runtime = session.client("bedrock-runtime", region_name=region)
    if is_inference_profile:
        control_response = bedrock.get_inference_profile(
            inferenceProfileIdentifier=target_id,
        )
        details = _check_profile_response(control_response)
    else:
        control_response = bedrock.get_foundation_model_availability(
            modelId=target_id,
        )
        details = _check_model_availability_response(control_response)

    if not use_converse:
        raise BedrockVerificationError(
            "Runtime verification requires the Bedrock Converse API. Enable Use Converse API and try again."
        )

    runtime.converse(
        modelId=target_id,
        messages=_PING_MESSAGES,
        inferenceConfig=_PING_INFERENCE_CONFIG,
    )
    return details | {"runtime_test": "converse"}


def _bedrock_url(region: str, path: str) -> str:
    return f"https://bedrock.{region}.amazonaws.com{path}"


def _bedrock_runtime_url(region: str, path: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com{path}"


def _extract_http_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("Message")
        error_type = payload.get("__type") or payload.get("code")
        if message and error_type:
            return f"{error_type}: {message}"
        if message:
            return str(message)
    return response.text or response.reason_phrase


async def _request_bedrock_api_key(
    *,
    method: str,
    url: str,
    api_key: str,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.request(method, url, headers=headers, json=json)
    if response.is_error:
        raise BedrockVerificationError(_extract_http_error(response))
    if not response.content:
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _verify_with_api_key(
    *,
    api_key: str,
    region: str,
    target_id: str,
    is_inference_profile: bool,
    use_converse: bool,
) -> dict[str, Any]:
    encoded_target = quote(target_id, safe="")
    if is_inference_profile:
        response = await _request_bedrock_api_key(
            method="GET",
            url=_bedrock_url(region, f"/inference-profiles/{encoded_target}"),
            api_key=api_key,
        )
        details = _check_profile_response(response)
    else:
        response = await _request_bedrock_api_key(
            method="GET",
            url=_bedrock_url(
                region, f"/foundation-model-availability/{encoded_target}"
            ),
            api_key=api_key,
        )
        details = _check_model_availability_response(response)

    if not use_converse:
        raise BedrockVerificationError(
            "Runtime verification requires the Bedrock Converse API. Enable Use Converse API and try again."
        )

    await _request_bedrock_api_key(
        method="POST",
        url=_bedrock_runtime_url(region, f"/model/{encoded_target}/converse"),
        api_key=api_key,
        json={
            "messages": _PING_MESSAGES,
            "inferenceConfig": _PING_INFERENCE_CONFIG,
        },
    )
    return details | {"runtime_test": "converse"}


async def verify_bedrock_catalog_target(
    *,
    credentials: dict[str, str],
    inference_profile_id: str | None,
    model_id: str | None,
    use_converse: bool,
) -> dict[str, Any]:
    """Verify a Bedrock catalog target with control-plane and runtime calls."""
    region = _get_required_region(credentials)
    target_id = inference_profile_id or model_id
    if target_id is None:
        raise BedrockVerificationError(
            "Provide exactly one of inference_profile_id or model_id."
        )

    resolved = await _resolve_bedrock_credentials(credentials)
    is_inference_profile = inference_profile_id is not None
    if api_key := resolved.get("AWS_BEARER_TOKEN_BEDROCK"):
        return await _verify_with_api_key(
            api_key=api_key,
            region=region,
            target_id=target_id,
            is_inference_profile=is_inference_profile,
            use_converse=use_converse,
        )

    try:
        return await asyncio.to_thread(
            _verify_with_boto3,
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
