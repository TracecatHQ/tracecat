"""Universal APIs for AWS services using boto3.

Provides a interface to Boto3's Client and Paginator APIs.
Supports role-based authentication and session management.
"""

import base64
import re
from typing import TYPE_CHECKING, Annotated, Any
from typing_extensions import Doc

import boto3
import aioboto3
from aiobotocore.response import StreamingBody
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from types_aiobotocore_sts.type_defs import (
        CredentialsTypeDef as AsyncCredentialsTypeDef,
    )
    from types_boto3_sts.type_defs import CredentialsTypeDef
else:
    AsyncCredentialsTypeDef = dict[str, Any]
    CredentialsTypeDef = dict[str, Any]

from tracecat_registry import (
    RegistrySecret,
    ctx,
    secrets,
    SecretNotFoundError,
    logger,
    registry,
)

_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY = "TRACECAT_AWS_EXTERNAL_ID"
_AWS_SERVICE_REGION_DOC = (
    "AWS service region to use for this request. Overrides the AWS_REGION secret "
    "for the service client."
)
_AWS_ROLE_ARN_PATTERN = re.compile(
    r"^arn:aws(?:-[a-z0-9-]+)?:iam::\d{12}:role/[\w+=,.@\-/]+$"
)
# AWS caps role-chained sessions (assumed from temporary credentials) at 1 hour.
_CHAINED_ROLE_MIN_DURATION_SECONDS = 900
_CHAINED_ROLE_MAX_DURATION_SECONDS = 3600

RoleArn = Annotated[
    str | None,
    Doc(
        "ARN of an IAM role to assume (role chaining) with the credentials from "
        "the AWS secret before calling the service, e.g. a role in another AWS "
        "account that trusts the configured role or user. Requires "
        "sts:AssumeRole on that role."
    ),
]
RoleSessionName = Annotated[
    str | None,
    Doc(
        "Session name recorded in CloudTrail for the chained AssumeRole. "
        "Defaults to a Tracecat workspace/run derived name."
    ),
]
ExternalId = Annotated[
    str | None,
    Doc(
        "External ID required by the chained role's trust policy, if any. "
        "Only used together with role_arn."
    ),
]
DurationSeconds = Annotated[
    int | None,
    Doc(
        "Duration of the chained role session in seconds (900-3600). AWS limits "
        "role chaining to a maximum of one hour. Only used together with role_arn."
    ),
]

aws_secret = RegistrySecret(
    name="aws",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
    ],
    optional=False,
)
"""AWS credentials.

- name: `aws`
- optional_keys:
    Either:
        - `AWS_ROLE_ARN` (recommended; Tracecat assumes the role on the host)
        - `AWS_ROLE_SESSION_NAME` (optional audit session label)
    Or:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_SESSION_TOKEN` (optional)
    And:
        - `AWS_REGION`

Tracecat automatically supplies the workspace-scoped AWS External ID used for
cross-account AssumeRole requests and uses a default STS session name when
`AWS_ROLE_SESSION_NAME` is unset.
"""


def _get_assume_role_external_id() -> str:
    if external_id := secrets.get_or_default(_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY):
        return external_id

    raise SecretNotFoundError(
        "AWS role assumption requires a Tracecat-provided workspace External ID."
    )


def _get_role_session_name() -> str:
    if session_name := secrets.get_or_default("AWS_ROLE_SESSION_NAME"):
        if not isinstance(session_name, str):
            raise TypeError("AWS_ROLE_SESSION_NAME must be a string when configured.")
        if session_name := session_name.strip():
            return session_name

    try:
        workspace_id = ctx.workspace_id
        run_id = ctx.run_id
    except RuntimeError:
        return "tracecat-session"

    parts = ["tracecat"]
    if workspace_id:
        parts.extend(["ws", workspace_id.replace("-", "")[:8]])
    if run_id:
        parts.extend(["run", run_id.replace("-", "")[:8]])
    return "-".join(parts)[:64]


def _get_region_name(region_name: str | None = None) -> str | None:
    """Return the per-call AWS region override or the configured secret region."""
    if region_name is not None:
        if not isinstance(region_name, str):
            raise TypeError("region_name must be a string when configured.")
        if region_name := region_name.strip():
            return region_name
        return None

    aws_region = secrets.get_or_default("AWS_REGION")
    if aws_region is not None and not isinstance(aws_region, str):
        raise TypeError("AWS_REGION must be a string when configured.")
    return aws_region.strip() if aws_region else None


async def get_temporary_credentials(
    role_arn: str,
    region_name: str | None = None,
) -> AsyncCredentialsTypeDef:
    sts_session = (
        aioboto3.Session(region_name=region_name) if region_name else aioboto3.Session()
    )
    async with sts_session.client("sts") as sts_client:
        response = await sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=_get_role_session_name(),
            ExternalId=_get_assume_role_external_id(),
        )
        creds = response["Credentials"]
    return creds


def get_sync_temporary_credentials(
    role_arn: str,
    region_name: str | None = None,
) -> CredentialsTypeDef:
    sts_session = (
        boto3.Session(region_name=region_name) if region_name else boto3.Session()
    )
    sts_client = sts_session.client("sts")
    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=_get_role_session_name(),
        ExternalId=_get_assume_role_external_id(),
    )
    creds = response["Credentials"]
    return creds


def _validate_chained_role_arn(role_arn: str) -> str:
    if not isinstance(role_arn, str):
        raise TypeError("role_arn must be a string.")
    role_arn = role_arn.strip()
    if not _AWS_ROLE_ARN_PATTERN.match(role_arn):
        raise ValueError(
            "Invalid role_arn: expected an IAM role ARN like "
            "'arn:aws:iam::123456789012:role/RoleName'."
        )
    return role_arn


def _build_chained_assume_role_params(
    role_arn: str,
    role_session_name: str | None,
    external_id: str | None,
    duration_seconds: int | None,
) -> dict[str, Any]:
    """Validate and build the ``AssumeRole`` kwargs for a chained role.

    Only fixed, known STS parameters are forwarded so callers cannot smuggle
    arbitrary credential fields into the request.
    """
    params: dict[str, Any] = {"RoleArn": _validate_chained_role_arn(role_arn)}

    if role_session_name is not None:
        if not isinstance(role_session_name, str):
            raise TypeError("role_session_name must be a string.")
        role_session_name = role_session_name.strip()
    params["RoleSessionName"] = (role_session_name or _get_role_session_name())[:64]

    if external_id is not None:
        if not isinstance(external_id, str):
            raise TypeError("external_id must be a string.")
        if external_id := external_id.strip():
            params["ExternalId"] = external_id

    if duration_seconds is not None:
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int):
            raise TypeError("duration_seconds must be an integer.")
        if not (
            _CHAINED_ROLE_MIN_DURATION_SECONDS
            <= duration_seconds
            <= _CHAINED_ROLE_MAX_DURATION_SECONDS
        ):
            raise ValueError(
                "duration_seconds must be between "
                f"{_CHAINED_ROLE_MIN_DURATION_SECONDS} and "
                f"{_CHAINED_ROLE_MAX_DURATION_SECONDS} seconds for role chaining."
            )
        params["DurationSeconds"] = duration_seconds

    return params


def _redact_assume_role_error(e: ClientError) -> RuntimeError:
    # STS error messages echo the ARN and caller identity; surface only the code.
    code = e.response.get("Error", {}).get("Code", "Unknown")
    return RuntimeError(f"Failed to assume chained AWS role (error code {code})")


async def assume_chained_role(
    base_session: aioboto3.Session,
    role_arn: str,
    *,
    role_session_name: str | None = None,
    external_id: str | None = None,
    duration_seconds: int | None = None,
    region_name: str | None = None,
) -> aioboto3.Session:
    """Assume ``role_arn`` using ``base_session`` and return a new session.

    This performs role chaining: the credentials already resolved from the AWS
    secret (static keys or a host-assumed role) call STS ``AssumeRole`` on a
    second role, typically in another account. Temporary credentials stay in
    memory only.
    """
    params = _build_chained_assume_role_params(
        role_arn, role_session_name, external_id, duration_seconds
    )
    try:
        async with base_session.client("sts") as sts_client:
            response = await sts_client.assume_role(**params)
    except ClientError as e:
        raise _redact_assume_role_error(e) from None
    creds = response["Credentials"]
    return aioboto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region_name,
    )


def assume_chained_role_sync(
    base_session: boto3.Session,
    role_arn: str,
    *,
    role_session_name: str | None = None,
    external_id: str | None = None,
    duration_seconds: int | None = None,
    region_name: str | None = None,
) -> boto3.Session:
    """Synchronous counterpart of :func:`assume_chained_role`."""
    params = _build_chained_assume_role_params(
        role_arn, role_session_name, external_id, duration_seconds
    )
    try:
        response = base_session.client("sts").assume_role(**params)
    except ClientError as e:
        raise _redact_assume_role_error(e) from None
    creds = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region_name,
    )


async def get_session(
    region_name: str | None = None,
    *,
    role_arn: str | None = None,
    role_session_name: str | None = None,
    external_id: str | None = None,
    duration_seconds: int | None = None,
) -> aioboto3.Session:
    """Build an aioboto3 session from secrets.

    Credential precedence:
    1. ``AWS_ROLE_ARN`` — STS AssumeRole (external ID + session name auto-set)
    2. ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` + ``AWS_SESSION_TOKEN``
    3. ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY``
    4. ``AWS_BEARER_TOKEN_BEDROCK`` (Bedrock-only bearer token)

    When ``role_arn`` is given, the resolved credentials then assume that role
    (role chaining) and the returned session carries the chained credentials.
    """
    aws_role_arn = secrets.get_or_default("AWS_ROLE_ARN")
    aws_region = _get_region_name(region_name)
    aws_access_key_id = secrets.get_or_default("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = secrets.get_or_default("AWS_SECRET_ACCESS_KEY")
    aws_session_token = secrets.get_or_default("AWS_SESSION_TOKEN")
    aws_bearer_token_bedrock = secrets.get_or_default("AWS_BEARER_TOKEN_BEDROCK")

    if aws_role_arn:
        creds = await get_temporary_credentials(aws_role_arn, region_name=aws_region)
        session = aioboto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=aws_region,
        )
    elif aws_access_key_id and aws_secret_access_key and aws_session_token:
        session = aioboto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=aws_region,
        )
    elif aws_access_key_id and aws_secret_access_key:
        logger.warning(
            "Session token not found. Defaulting to IAM credentials (not recommended)."
        )
        session = aioboto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=aws_region,
        )
    elif aws_bearer_token_bedrock:
        session = aioboto3.Session(region_name=aws_region)
    else:
        # NOTE: This is critical. We must not allow Boto3's default behavior of
        # using the AWS credentials from the environment.
        raise SecretNotFoundError("No AWS credentials found.")

    if role_arn:
        session = await assume_chained_role(
            session,
            role_arn,
            role_session_name=role_session_name,
            external_id=external_id,
            duration_seconds=duration_seconds,
            region_name=aws_region,
        )

    return session


def get_sync_session(
    region_name: str | None = None,
    *,
    role_arn: str | None = None,
    role_session_name: str | None = None,
    external_id: str | None = None,
    duration_seconds: int | None = None,
) -> boto3.Session:
    """Build a boto3 session from secrets.

    Credential precedence:
    1. ``AWS_ROLE_ARN`` — STS AssumeRole (external ID + session name auto-set)
    2. ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` + ``AWS_SESSION_TOKEN``
    3. ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY``
    4. ``AWS_BEARER_TOKEN_BEDROCK`` (Bedrock-only bearer token)

    When ``role_arn`` is given, the resolved credentials then assume that role
    (role chaining) and the returned session carries the chained credentials.
    """
    aws_role_arn = secrets.get_or_default("AWS_ROLE_ARN")
    aws_region = _get_region_name(region_name)
    aws_access_key_id = secrets.get_or_default("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = secrets.get_or_default("AWS_SECRET_ACCESS_KEY")
    aws_session_token = secrets.get_or_default("AWS_SESSION_TOKEN")
    aws_bearer_token_bedrock = secrets.get_or_default("AWS_BEARER_TOKEN_BEDROCK")

    if aws_role_arn:
        creds = get_sync_temporary_credentials(aws_role_arn, region_name=aws_region)
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=aws_region,
        )
    elif aws_access_key_id and aws_secret_access_key and aws_session_token:
        session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=aws_region,
        )
    elif aws_access_key_id and aws_secret_access_key:
        logger.warning(
            "Session token not found. Defaulting to IAM credentials (not recommended)."
        )
        session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=aws_region,
        )
    elif aws_bearer_token_bedrock:
        session = boto3.Session(region_name=aws_region)
    else:
        # NOTE: This is critical. We must not allow Boto3's default behavior of
        # using the AWS credentials from the environment.
        raise SecretNotFoundError("No AWS credentials found.")

    if role_arn:
        session = assume_chained_role_sync(
            session,
            role_arn,
            role_session_name=role_session_name,
            external_id=external_id,
            duration_seconds=duration_seconds,
            region_name=aws_region,
        )

    return session


_STREAMING_BODY_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


async def _read_streaming_values(obj: Any) -> Any:
    """Recursively read StreamingBody and bytes values in a boto3 response.

    Content is decoded as UTF-8, falling back to base64 for binary data.
    """
    if isinstance(obj, StreamingBody):
        content = await obj.read(_STREAMING_BODY_MAX_BYTES)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(content).decode("ascii")
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(obj).decode("ascii")
    if isinstance(obj, dict):
        return {k: await _read_streaming_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [await _read_streaming_values(item) for item in obj]
    return obj


@registry.register(
    default_title="Call method",
    description="Instantiate a Boto3 client and call an AWS Boto3 API method.",
    display_group="AWS Boto3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/guide/clients.html",
    namespace="tools.aws_boto3",
    secrets=[aws_secret],
)
async def call_api(
    service_name: Annotated[
        str,
        Doc("AWS service name e.g. 's3', 'ec2', 'guardduty'."),
    ],
    method_name: Annotated[
        str,
        Doc("Method name e.g. 'list_buckets', 'list_instances'"),
    ],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Doc("Parameters for the API method."),
    ] = None,
    role_arn: RoleArn = None,
    role_session_name: RoleSessionName = None,
    external_id: ExternalId = None,
    duration_seconds: DurationSeconds = None,
) -> dict[str, Any]:
    params = params or {}
    session = await get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    async with session.client(service_name, endpoint_url=endpoint_url) as client:  # type: ignore
        response = await getattr(client, method_name)(**params)
        return await _read_streaming_values(response)


@registry.register(
    default_title="Call paginator",
    description="Instantiate a Boto3 paginator and call a paginated AWS API method.",
    display_group="AWS Boto3",
    doc_url="https://docs.aws.amazon.com/boto3/latest/guide/paginators.html",
    namespace="tools.aws_boto3",
    secrets=[aws_secret],
)
async def call_paginated_api(
    service_name: Annotated[
        str,
        Doc("AWS service name e.g. 's3', 'ec2', 'guardduty'."),
    ],
    paginator_name: Annotated[
        str,
        Doc("Paginator name e.g. 'list_objects_v2', 'describe_instances'."),
    ],
    endpoint_url: Annotated[
        str | None,
        Doc("Endpoint URL for the AWS service."),
    ] = None,
    region_name: Annotated[
        str | None,
        Doc(_AWS_SERVICE_REGION_DOC),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Doc("Parameters for the API paginator."),
    ] = None,
    role_arn: RoleArn = None,
    role_session_name: RoleSessionName = None,
    external_id: ExternalId = None,
    duration_seconds: DurationSeconds = None,
) -> list[dict[str, Any]]:
    params = params or {}
    session = await get_session(
        region_name=region_name,
        role_arn=role_arn,
        role_session_name=role_session_name,
        external_id=external_id,
        duration_seconds=duration_seconds,
    )
    async with session.client(service_name, endpoint_url=endpoint_url) as client:  # type: ignore
        paginator = client.get_paginator(paginator_name)
        pages = paginator.paginate(**params)

        results = []
        async for page in pages:
            results.append(await _read_streaming_values(page))

    return results
