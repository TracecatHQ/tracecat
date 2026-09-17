"""Explicit Bedrock authentication; never use ambient credentials for inference."""

import asyncio
from contextlib import closing

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
)

from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.types import SearchScope


def _assume_role(values: dict[str, str], scope: SearchScope) -> dict[str, str]:
    # Ambient workload credentials are only the STS caller for an explicitly
    # configured role, matching the existing Bedrock provider's trust model.
    with closing(
        boto3.Session().client(
            "sts",
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 1},
            ),
        )
    ) as sts:
        response = sts.assume_role(
            RoleArn=values["AWS_ROLE_ARN"],
            RoleSessionName=values.get("AWS_ROLE_SESSION_NAME") or "tracecat-search",
            ExternalId=build_workspace_external_id(scope.workspace_id),
        )
    credentials = response["Credentials"]
    return values | {
        "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
        "AWS_SESSION_TOKEN": credentials["SessionToken"],
    }


async def request_headers(
    values: dict[str, str], scope: SearchScope, url: str, body: bytes
) -> dict[str, str]:
    """Sign using the configured role/static keys, or use the configured API key."""
    if values.get("AWS_ROLE_ARN"):
        try:
            values = await asyncio.to_thread(_assume_role, values, scope)
        except ClientError as exc:
            # STS uses HTTP 400 for some transient errors too. Classify its typed
            # error code, never the status alone or a potentially sensitive message.
            match exc.response.get("Error", {}).get("Code"):
                case (
                    "AccessDenied"
                    | "AccessDeniedException"
                    | "ValidationError"
                    | "ValidationException"
                    | "InvalidClientTokenId"
                    | "SignatureDoesNotMatch"
                    | "UnrecognizedClientException"
                ):
                    code = EmbeddingErrorCode.CREDENTIAL_INVALID
                case (
                    "Throttling"
                    | "ThrottlingException"
                    | "TooManyRequestsException"
                    | "RequestLimitExceeded"
                ):
                    code = EmbeddingErrorCode.RATE_LIMITED
                case _:
                    # Includes expired ambient credentials, which may refresh.
                    code = EmbeddingErrorCode.UNAVAILABLE
            error = EmbeddingError(code)
        except (NoCredentialsError, PartialCredentialsError, ParamValidationError):
            error = EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
        else:
            error = None
        # Do not retain the SDK exception or its credential-bearing context.
        if error is not None:
            raise error
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    access, secret = (
        values.get("AWS_ACCESS_KEY_ID"),
        values.get("AWS_SECRET_ACCESS_KEY"),
    )
    if access and secret:
        request = AWSRequest(method="POST", url=url, data=body, headers=headers)
        SigV4Auth(
            Credentials(access, secret, values.get("AWS_SESSION_TOKEN")),
            "bedrock",
            values["AWS_REGION"],
        ).add_auth(request)
        return dict(request.headers.items())
    if access or secret or values.get("AWS_SESSION_TOKEN"):
        raise EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
    if bearer := values.get("AWS_BEARER_TOKEN_BEDROCK"):
        return headers | {"Authorization": f"Bearer {bearer}"}
    raise EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
