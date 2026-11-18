"""AWS Secrets Manager provider for external secret stores.

Only this module talks to AWS. It assumes the store role with the Tracecat
workload identity (IRSA / task role) and a persisted external ID and reads
the ``AWSCURRENT`` version of a secret. Mapping values onto keys, sanitizing
failures and clearing values is core's job (:mod:`tracecat_ee.secrets.resolver`);
the functions below that predate the provider interface are thin wrappers
over it, kept so their public names and behaviour stay as they were.
"""

from __future__ import annotations

import asyncio
import re
import secrets as std_secrets
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

import aioboto3
from aiobotocore.config import AioConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
    PartialCredentialsError,
)

from tracecat.exceptions import TracecatConflictError
from tracecat.secrets.enums import (
    SecretResolutionErrorCode,
    SecretStoreProvider,
)
from tracecat.secrets.schemas import (
    AWS_SECRET_ID_PATTERN,
    AwsSecretsManagerStoreConfig,
    AwsSecretsManagerStoreCreate,
    AwsSecretsManagerStoreUpdate,
    aws_partition_for_region,
    check_aws_partition,
)
from tracecat.secrets.types import (
    CheckResult,
    ExternalSecretReference,
    Failure,
    FetchOutcome,
    FetchResult,
    StoreContext,
)
from tracecat_ee.secrets.resolver import (
    SecretResolutionError,
    check_reference,
    fetch_and_project,
    project_value,
)

_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 10
_MAX_ATTEMPTS = 3
_ROLE_SESSION_NAME_MAX_LEN = 64
_AWSCURRENT = "AWSCURRENT"
_EXTERNAL_ID_BYTES = 24

_STS_CODE_TO_ERROR: dict[str, SecretResolutionErrorCode] = {
    "AccessDenied": SecretResolutionErrorCode.ASSUME_ROLE_FAILED,
    "AccessDeniedException": SecretResolutionErrorCode.ASSUME_ROLE_FAILED,
    "Throttling": SecretResolutionErrorCode.THROTTLED,
    "ThrottlingException": SecretResolutionErrorCode.THROTTLED,
}

_SECRETS_CODE_TO_ERROR: dict[str, SecretResolutionErrorCode] = {
    "AccessDeniedException": SecretResolutionErrorCode.ACCESS_DENIED,
    "ResourceNotFoundException": SecretResolutionErrorCode.NOT_FOUND,
    "DecryptionFailure": SecretResolutionErrorCode.DECRYPTION_FAILED,
    "ThrottlingException": SecretResolutionErrorCode.THROTTLED,
    "TooManyRequestsException": SecretResolutionErrorCode.THROTTLED,
    "InvalidRequestException": SecretResolutionErrorCode.INVALID_MAPPING,
    "InvalidParameterException": SecretResolutionErrorCode.INVALID_MAPPING,
}


class AwsSecretResolutionError(SecretResolutionError):
    """Sanitized failure while resolving an AWS-backed workspace secret."""

    def __init__(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        aws_error_code: str | None = None,
    ) -> None:
        super().__init__(
            code,
            alias=alias,
            environment=environment,
            provider_label=AwsSecretsManagerBackend.display_name,
            vendor_code=aws_error_code,
            vendor_code_field="aws_error_code",
        )

    @property
    def aws_error_code(self) -> str | None:
        """The AWS error code, for logs. Never AWS message text."""
        return self.vendor_code


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    """Result of one remote read. Exactly one of value/failure is set."""

    value: str | None = None
    failure: SecretResolutionErrorCode | None = None
    aws_error_code: str | None = None


def _client_config() -> AioConfig:
    return AioConfig(
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_READ_TIMEOUT_SECONDS,
        retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
    )


def _role_session_name(store: StoreContext | ExternalSecretReference) -> str:
    store_short = str(store.store_id).replace("-", "")[:12]
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
    error: ClientError, table: dict[str, SecretResolutionErrorCode]
) -> tuple[SecretResolutionErrorCode, str]:
    code = str(error.response.get("Error", {}).get("Code", "Unknown"))
    return table.get(code, SecretResolutionErrorCode.UNKNOWN), code


async def _fetch_secret_string(store: StoreContext, secret_id: str) -> _FetchOutcome:
    """Assume the store role and read the AWSCURRENT SecretString.

    Returns an outcome instead of raising so no exception that saw the SDK
    response leaves this function.
    """
    config_model = _aws_config(store.config)
    role_arn = config_model.role_arn
    # Stores without a role ARN can never be enabled; treat them as disabled.
    if not store.enabled or role_arn is None:
        return _FetchOutcome(failure=SecretResolutionErrorCode.STORE_DISABLED)
    if not reference_region_matches(secret_id, config_model.region):
        return _FetchOutcome(failure=SecretResolutionErrorCode.REGION_MISMATCH)

    config = _client_config()
    session = aioboto3.Session(region_name=config_model.region)
    try:
        async with session.client("sts", config=config) as sts_client:
            assumed = await sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName=_role_session_name(store),
                ExternalId=config_model.external_id,
            )
    except ClientError as e:
        failure, aws_code = _classify_client_error(e, _STS_CODE_TO_ERROR)
        if failure == SecretResolutionErrorCode.UNKNOWN:
            failure = SecretResolutionErrorCode.ASSUME_ROLE_FAILED
        return _FetchOutcome(failure=failure, aws_error_code=aws_code)
    except (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError):
        # The executor has no usable workload identity to assume the role with.
        return _FetchOutcome(failure=SecretResolutionErrorCode.ASSUME_ROLE_FAILED)
    except (BotoCoreError, TimeoutError):
        return _FetchOutcome(failure=SecretResolutionErrorCode.TIMEOUT)

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
                SecretId=secret_id, VersionStage=_AWSCURRENT
            )
    except ClientError as e:
        failure, aws_code = _classify_client_error(e, _SECRETS_CODE_TO_ERROR)
        return _FetchOutcome(failure=failure, aws_error_code=aws_code)
    except (BotoCoreError, TimeoutError):
        return _FetchOutcome(failure=SecretResolutionErrorCode.TIMEOUT)

    secret_string = response.get("SecretString")
    if secret_string is None:
        return _FetchOutcome(failure=SecretResolutionErrorCode.BINARY_VALUE)
    return _FetchOutcome(value=secret_string)


def _aws_config(config: object) -> AwsSecretsManagerStoreConfig:
    if not isinstance(config, AwsSecretsManagerStoreConfig):
        raise TypeError("AWS Secrets Manager store requires an AWS config")
    return config


def _as_outcome(outcome: _FetchOutcome) -> FetchOutcome:
    if outcome.failure is not None or outcome.value is None:
        return Failure(
            outcome.failure or SecretResolutionErrorCode.UNKNOWN,
            outcome.aws_error_code,
        )
    return outcome.value


def project_secret_string(
    reference: ExternalSecretReference, secret_string: str
) -> dict[str, str] | SecretResolutionErrorCode:
    """Map a SecretString onto declared output keys (see ``project_value``)."""
    return project_value(reference.mapping, secret_string)


def _lookup(_: SecretStoreProvider) -> AwsSecretsManagerBackend:
    return _BACKEND


async def resolve_aws_secret_references(
    references: Sequence[ExternalSecretReference],
) -> dict[str, dict[str, str]]:
    """Resolve AWS-backed aliases to ``{alias: {key: value}}``.

    Any failure raises a sanitized :class:`AwsSecretResolutionError` with no
    chained cause.
    """
    if not references:
        return {}
    resolved, failure = await fetch_and_project(references, _lookup)
    if failure is not None:
        error = AwsSecretResolutionError(
            failure.failure.code,
            alias=failure.reference.alias,
            environment=failure.reference.environment,
            aws_error_code=failure.failure.vendor_code,
        )
        raise error
    return resolved


async def check_aws_secret_reference(
    reference: ExternalSecretReference,
) -> CheckResult:
    """Verify a reference resolves without returning its value."""
    return await check_reference(reference, _lookup)


def collect_output_keys(references: Iterable[ExternalSecretReference]) -> list[str]:
    """Return declared output keys across references without remote access."""
    keys: list[str] = []
    for reference in references:
        keys.extend(reference.mapping.output_keys())
    return keys


def generate_store_external_id() -> str:
    """Generate an opaque, server-owned AssumeRole external ID."""
    return f"tracecat-{std_secrets.token_urlsafe(_EXTERNAL_ID_BYTES)}"


class AwsSecretsManagerBackend:
    """AWS Secrets Manager implementation of ``SecretStoreBackend``."""

    provider = SecretStoreProvider.AWS_SECRETS_MANAGER
    display_name = "AWS"
    # AWS uses the workload identity.
    requires_access_token = False

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
        *,
        references: Collection[str] = (),
    ) -> AwsSecretsManagerStoreConfig:
        """Apply role/region updates. The external ID is never changed.

        Saved ARN references were validated against the current region, so
        the region cannot change while any exist. Friendly names follow the
        store's region and do not block the change.
        """
        updated = config.model_copy(
            update=params.model_dump(
                exclude_unset=True, exclude_none=True, exclude={"provider"}
            )
        )
        if updated.role_arn is not None:
            check_aws_partition(updated.role_arn, updated.region)
        if updated.region != config.region and any(
            is_secret_arn(reference) for reference in references
        ):
            raise TracecatConflictError(
                "Update or remove secrets that reference this store by ARN"
                " before changing its region."
            )
        return updated

    def resolution_error(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        vendor_code: str | None,
    ) -> AwsSecretResolutionError:
        """Runs keep raising the AWS error type, with ``aws_error_code``."""
        return AwsSecretResolutionError(
            code, alias=alias, environment=environment, aws_error_code=vendor_code
        )

    def is_ready(self, config: AwsSecretsManagerStoreConfig) -> bool:
        """A store can be enabled only once its role ARN is set."""
        return config.role_arn is not None

    async def check_config(self, config: AwsSecretsManagerStoreConfig) -> None:
        """AWS endpoints are fixed per region; nothing to check."""

    def validate_reference(
        self, config: AwsSecretsManagerStoreConfig, key: str
    ) -> None:
        """Reject a malformed name or ARN, or one from another region or partition."""
        if not re.fullmatch(AWS_SECRET_ID_PATTERN, key):
            raise ValueError(
                "Enter an AWS Secrets Manager secret name or a full secret ARN."
            )
        if not reference_region_matches(key, config.region):
            raise ValueError(
                f"Secret ARN region must match the store region {config.region!r}."
            )
        partition = (
            config.role_arn.split(":")[1]
            if config.role_arn is not None
            else aws_partition_for_region(config.region)
        )
        if (
            partition is not None
            and is_secret_arn(key)
            and key.split(":")[1] != partition
        ):
            raise ValueError(f"Secret ARN partition must be {partition!r}.")

    async def fetch(
        self, store: StoreContext, references: Sequence[str]
    ) -> FetchResult:
        """Read every distinct reference concurrently, one AssumeRole each."""
        outcomes = await asyncio.gather(
            *(_fetch_secret_string(store, reference) for reference in references)
        )
        return FetchResult(
            outcomes={
                reference: _as_outcome(outcome)
                for reference, outcome in zip(references, outcomes, strict=True)
            }
        )


_BACKEND = AwsSecretsManagerBackend()
