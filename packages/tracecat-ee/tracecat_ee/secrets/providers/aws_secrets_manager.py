"""Runtime resolver for AWS Secrets Manager backed workspace secrets.

Only this module talks to AWS. It assumes the store role with the Tracecat
workload identity (IRSA / task role) and a persisted external ID, reads the
``AWSCURRENT`` version of a full secret ARN, and projects the value onto the
declared output keys. Remote payloads never reach logs, error messages, or
exception chains: every failure is re-raised as a sanitized
:class:`AwsSecretResolutionError` after the original handler has exited.
"""

from __future__ import annotations

import asyncio
import secrets as std_secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import aioboto3
import orjson
from aiobotocore.config import AioConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
    PartialCredentialsError,
)

from tracecat.exceptions import TracecatCredentialsError
from tracecat.logger import logger
from tracecat.secrets.enums import (
    AwsSecretMappingMode,
    AwsSecretResolutionErrorCode,
    SecretStoreProvider,
)
from tracecat.secrets.schemas import (
    AwsSecretsManagerStoreConfig,
    AwsSecretsManagerStoreCreate,
    AwsSecretsManagerStoreUpdate,
    check_aws_partition,
)
from tracecat.secrets.types import (
    CheckResult,
    ExternalSecretReference,
)

_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 10
_MAX_ATTEMPTS = 3
_ROLE_SESSION_NAME_MAX_LEN = 64
_AWSCURRENT = "AWSCURRENT"
_EXTERNAL_ID_BYTES = 24

_STS_CODE_TO_ERROR: dict[str, AwsSecretResolutionErrorCode] = {
    "AccessDenied": AwsSecretResolutionErrorCode.ASSUME_ROLE_FAILED,
    "AccessDeniedException": AwsSecretResolutionErrorCode.ASSUME_ROLE_FAILED,
    "Throttling": AwsSecretResolutionErrorCode.THROTTLED,
    "ThrottlingException": AwsSecretResolutionErrorCode.THROTTLED,
}

_SECRETS_CODE_TO_ERROR: dict[str, AwsSecretResolutionErrorCode] = {
    "AccessDeniedException": AwsSecretResolutionErrorCode.ACCESS_DENIED,
    "ResourceNotFoundException": AwsSecretResolutionErrorCode.NOT_FOUND,
    "DecryptionFailure": AwsSecretResolutionErrorCode.DECRYPTION_FAILED,
    "ThrottlingException": AwsSecretResolutionErrorCode.THROTTLED,
    "TooManyRequestsException": AwsSecretResolutionErrorCode.THROTTLED,
    "InvalidRequestException": AwsSecretResolutionErrorCode.INVALID_MAPPING,
    "InvalidParameterException": AwsSecretResolutionErrorCode.INVALID_MAPPING,
}


class AwsSecretResolutionError(TracecatCredentialsError):
    """Sanitized failure while resolving an AWS-backed workspace secret."""

    def __init__(
        self,
        code: AwsSecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        aws_error_code: str | None = None,
    ) -> None:
        self.code = code
        self.alias = alias
        self.environment = environment
        self.aws_error_code = aws_error_code
        suffix = f" (AWS error code {aws_error_code})" if aws_error_code else ""
        super().__init__(
            f"Failed to resolve AWS-backed secret {alias!r} in environment "
            f"{environment!r}: {code.value}{suffix}",
            detail={
                "secret_name": alias,
                "environment": environment,
                "error_code": code.value,
                "aws_error_code": aws_error_code,
            },
        )


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    """Result of one remote read. Exactly one of value/failure is set."""

    value: str | None = None
    failure: AwsSecretResolutionErrorCode | None = None
    aws_error_code: str | None = None


def _client_config() -> AioConfig:
    return AioConfig(
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_READ_TIMEOUT_SECONDS,
        retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
    )


def _role_session_name(reference: ExternalSecretReference) -> str:
    store_short = str(reference.store_id).replace("-", "")[:12]
    return f"tracecat-secretstore-{store_short}"[:_ROLE_SESSION_NAME_MAX_LEN]


def is_secret_arn(secret_id: str) -> bool:
    """Whether a ``SecretId`` is a full ARN rather than a friendly name."""
    return secret_id.startswith("arn:")


def arn_region(secret_arn: str) -> str | None:
    """Extract the region component of a Secrets Manager ARN."""
    parts = secret_arn.split(":")
    if len(parts) < 6 or parts[2] != "secretsmanager":
        return None
    return parts[3] or None


def reference_region_matches(secret_id: str, region: str) -> bool:
    """Friendly names are region-scoped by the store; ARNs must agree with it."""
    if not is_secret_arn(secret_id):
        return True
    return arn_region(secret_id) == region


def _classify_client_error(
    error: ClientError, table: dict[str, AwsSecretResolutionErrorCode]
) -> tuple[AwsSecretResolutionErrorCode, str]:
    code = str(error.response.get("Error", {}).get("Code", "Unknown"))
    return table.get(code, AwsSecretResolutionErrorCode.UNKNOWN), code


async def _fetch_secret_string(reference: ExternalSecretReference) -> _FetchOutcome:
    """Assume the store role and read the AWSCURRENT SecretString.

    Returns an outcome instead of raising so the caller can raise the
    sanitized error outside of any handler that saw the SDK response.
    """
    if not reference.store_enabled:
        return _FetchOutcome(failure=AwsSecretResolutionErrorCode.STORE_DISABLED)
    if not reference_region_matches(reference.key, reference.store_config.region):
        return _FetchOutcome(failure=AwsSecretResolutionErrorCode.REGION_MISMATCH)

    config = _client_config()
    session = aioboto3.Session(region_name=reference.store_config.region)
    try:
        async with session.client("sts", config=config) as sts_client:
            assumed = await sts_client.assume_role(
                RoleArn=reference.store_config.role_arn,
                RoleSessionName=_role_session_name(reference),
                ExternalId=reference.store_config.external_id,
            )
    except ClientError as e:
        failure, aws_code = _classify_client_error(e, _STS_CODE_TO_ERROR)
        if failure == AwsSecretResolutionErrorCode.UNKNOWN:
            failure = AwsSecretResolutionErrorCode.ASSUME_ROLE_FAILED
        return _FetchOutcome(failure=failure, aws_error_code=aws_code)
    except (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError):
        # The executor has no usable workload identity to assume the role with.
        return _FetchOutcome(failure=AwsSecretResolutionErrorCode.ASSUME_ROLE_FAILED)
    except (BotoCoreError, TimeoutError):
        return _FetchOutcome(failure=AwsSecretResolutionErrorCode.TIMEOUT)

    credentials = assumed["Credentials"]
    try:
        async with session.client(
            "secretsmanager",
            config=config,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        ) as sm_client:
            response = await sm_client.get_secret_value(
                SecretId=reference.key, VersionStage=_AWSCURRENT
            )
    except ClientError as e:
        failure, aws_code = _classify_client_error(e, _SECRETS_CODE_TO_ERROR)
        return _FetchOutcome(failure=failure, aws_error_code=aws_code)
    except (BotoCoreError, TimeoutError):
        return _FetchOutcome(failure=AwsSecretResolutionErrorCode.TIMEOUT)

    secret_string = response.get("SecretString")
    if secret_string is None:
        return _FetchOutcome(failure=AwsSecretResolutionErrorCode.BINARY_VALUE)
    return _FetchOutcome(value=secret_string)


def project_secret_string(
    reference: ExternalSecretReference, secret_string: str
) -> dict[str, str] | AwsSecretResolutionErrorCode:
    """Map a SecretString onto declared output keys.

    Whole-string mode returns the value verbatim under the single declared key.
    JSON mode requires a top-level object whose selected fields exist and are
    strings. Returns an error code instead of raising so no payload-bearing
    exception is ever created.
    """
    mapping = reference.mapping
    if mapping.mode == AwsSecretMappingMode.WHOLE_STRING:
        return {mapping.keys[0]: secret_string}

    try:
        parsed: Any = orjson.loads(secret_string)
    except orjson.JSONDecodeError:
        return AwsSecretResolutionErrorCode.MALFORMED_JSON
    if not isinstance(parsed, dict):
        return AwsSecretResolutionErrorCode.MALFORMED_JSON

    projected: dict[str, str] = {}
    for selector in mapping.fields:
        if selector.field not in parsed:
            return AwsSecretResolutionErrorCode.MISSING_FIELD
        field_value = parsed[selector.field]
        if not isinstance(field_value, str):
            return AwsSecretResolutionErrorCode.NON_STRING_FIELD
        projected[selector.key] = field_value
    return projected


async def resolve_aws_secret_references(
    references: Sequence[ExternalSecretReference],
) -> dict[str, dict[str, str]]:
    """Resolve AWS-backed aliases to ``{alias: {key: value}}``.

    Remote reads are deduplicated per (store ID, secret reference) within this call
    and never cached across calls. Any failure raises a sanitized
    :class:`AwsSecretResolutionError` with no chained cause.
    """
    if not references:
        return {}

    resolved, error = await _fetch_and_project(references)
    if error is not None:
        logger.warning(
            "AWS-backed secret resolution failed",
            alias=error.alias,
            environment=error.environment,
            error_code=error.code.value,
            aws_error_code=error.aws_error_code,
        )
        raise error
    return resolved


async def _fetch_and_project(
    references: Sequence[ExternalSecretReference],
) -> tuple[dict[str, dict[str, str]], AwsSecretResolutionError | None]:
    """Fetch and map every reference, returning ``(resolved, error)``.

    Never raises. On failure the returned mapping is empty, so the frame that
    raises the sanitized error holds no fetched or partially projected values
    in its locals.
    """
    unique: dict[tuple[UUID, str], ExternalSecretReference] = {}
    for reference in references:
        unique.setdefault(reference.fetch_key, reference)

    logger.info(
        "Resolving AWS-backed secrets",
        references=len(references),
        remote_reads=len(unique),
    )
    outcomes = await asyncio.gather(
        *(_fetch_secret_string(reference) for reference in unique.values())
    )
    fetched = dict(zip(unique.keys(), outcomes, strict=True))

    resolved: dict[str, dict[str, str]] = {}
    for reference in references:
        outcome = fetched[reference.fetch_key]
        if outcome.failure is not None or outcome.value is None:
            return {}, AwsSecretResolutionError(
                outcome.failure or AwsSecretResolutionErrorCode.UNKNOWN,
                alias=reference.alias,
                environment=reference.environment,
                aws_error_code=outcome.aws_error_code,
            )
        projected = project_secret_string(reference, outcome.value)
        if isinstance(projected, AwsSecretResolutionErrorCode):
            return {}, AwsSecretResolutionError(
                projected, alias=reference.alias, environment=reference.environment
            )
        resolved.setdefault(reference.alias, {}).update(projected)
    return resolved, None


async def check_aws_secret_reference(
    reference: ExternalSecretReference,
) -> CheckResult:
    """Verify a reference resolves without returning its value.

    The fetched value is discarded immediately; only the resolved key names are
    returned.
    """
    outcome = await _fetch_secret_string(reference)
    if outcome.failure is not None or outcome.value is None:
        return CheckResult(
            ok=False,
            error_code=outcome.failure or AwsSecretResolutionErrorCode.UNKNOWN,
            provider_error_code=outcome.aws_error_code,
        )
    projected = project_secret_string(reference, outcome.value)
    if isinstance(projected, AwsSecretResolutionErrorCode):
        return CheckResult(ok=False, error_code=projected)
    return CheckResult(ok=True, resolved_keys=sorted(projected.keys()))


def collect_output_keys(references: Iterable[ExternalSecretReference]) -> list[str]:
    """Return declared output keys across references without AWS access."""
    keys: list[str] = []
    for reference in references:
        keys.extend(reference.mapping.output_keys())
    return keys


def generate_store_external_id() -> str:
    """Generate an opaque, server-owned AssumeRole external ID."""
    return f"tracecat-{std_secrets.token_urlsafe(_EXTERNAL_ID_BYTES)}"


class AwsSecretsManagerBackend:
    """AWS Secrets Manager implementation of :class:`SecretStoreBackend`."""

    provider = SecretStoreProvider.AWS_SECRETS_MANAGER

    def new_config(
        self, params: AwsSecretsManagerStoreCreate
    ) -> AwsSecretsManagerStoreConfig:
        """Build a store config with a freshly generated external ID."""
        return AwsSecretsManagerStoreConfig(
            role_arn=params.role_arn,
            region=params.region,
            external_id=generate_store_external_id(),
        )

    def update_config(
        self,
        config: AwsSecretsManagerStoreConfig,
        params: AwsSecretsManagerStoreUpdate,
    ) -> AwsSecretsManagerStoreConfig:
        """Apply role/region updates. The external ID is never changed."""
        updated = config.model_copy(
            update=params.model_dump(exclude_unset=True, exclude_none=True)
        )
        check_aws_partition(updated.role_arn, updated.region)
        return updated

    def validate_reference(
        self, config: AwsSecretsManagerStoreConfig, key: str
    ) -> None:
        """Reject an ARN whose region or partition disagrees with the store."""
        if not reference_region_matches(key, config.region):
            raise ValueError(
                f"Secret ARN region must match the store region {config.region!r}."
            )
        partition = config.role_arn.split(":")[1]
        if is_secret_arn(key) and key.split(":")[1] != partition:
            raise ValueError(f"Secret ARN partition must be {partition!r}.")

    async def resolve(
        self, references: Sequence[ExternalSecretReference]
    ) -> dict[str, dict[str, str]]:
        """Resolve AWS-backed aliases to their declared output keys."""
        return await resolve_aws_secret_references(references)

    async def check(self, reference: ExternalSecretReference) -> CheckResult:
        """Verify one AWS-backed reference resolves."""
        return await check_aws_secret_reference(reference)
