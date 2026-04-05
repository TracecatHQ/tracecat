from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import aioboto3
from botocore.exceptions import ClientError

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunContext
from tracecat.exceptions import TracecatCredentialsError
from tracecat.parse import traverse_leaves
from tracecat.secrets import secrets_manager
from tracecat.secrets.secrets_manager import _AWS_EXTERNAL_ID_SECRET_KEY


@dataclass(frozen=True)
class SecretEnvProjection:
    """Runtime-ready secret environment derived from raw secrets."""

    env: dict[str, str]
    mask_values: set[str]


@runtime_checkable
class SecretPreprocessor(Protocol):
    """Transforms raw secrets into a runtime-ready secret view."""

    def matches(self, secrets: Mapping[str, Any]) -> bool:
        """Return True when this preprocessor should run for the given secrets."""
        ...

    async def preprocess(
        self,
        *,
        secrets: Mapping[str, Any],
        role: Role,
        run_context: RunContext,
    ) -> dict[str, Any]:
        """Return a transformed secret view for runtime injection."""
        ...


async def _assume_role_via_irsa(
    *,
    role_arn: str,
    external_id: str,
    workspace_id: str,
    run_id: str,
    role_session_name: str | None = None,
) -> dict[str, str]:
    """Assume an AWS IAM role using the host IRSA identity."""
    if role_session_name:
        session_name = role_session_name
    else:
        ws_short = workspace_id.replace("-", "")[:8]
        run_short = run_id.replace("-", "")[:8]
        session_name = f"tracecat-ws-{ws_short}-run-{run_short}"[:64]

    try:
        session = aioboto3.Session()
        async with session.client("sts") as sts_client:
            response = await sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                ExternalId=external_id,
            )
    except ClientError as e:
        raise TracecatCredentialsError(
            f"Failed to assume AWS role '{role_arn}': {e}"
        ) from e

    creds = response["Credentials"]
    return {
        "AccessKeyId": creds["AccessKeyId"],
        "SecretAccessKey": creds["SecretAccessKey"],
        "SessionToken": creds["SessionToken"],
    }


class AwsAssumeRoleSecretPreprocessor:
    """Rewrites protected AWS role secrets into temporary session credentials."""

    ROLE_ARN_PATTERN = re.compile(
        r"^arn:aws(?:-[a-z0-9-]+)?:iam::\d{12}:role/[\w+=,.@\-/]+$"
    )
    PROTECTED_SECRET_NAMES = frozenset({"aws", "amazon_s3"})

    def matches(self, secrets: Mapping[str, Any]) -> bool:
        """Return True when any protected AWS secret namespace is present."""
        return bool(self.PROTECTED_SECRET_NAMES & secrets.keys())

    def _get_secret_values(
        self, projected_secrets: dict[str, Any], secret_name: str
    ) -> dict[str, Any] | None:
        """Return the mutable secret payload for a protected secret name."""
        if not isinstance(secret_values := projected_secrets.get(secret_name), dict):
            return None
        return secret_values

    def _validate_aws_profile(
        self, secret_values: dict[str, Any], secret_name: str
    ) -> None:
        """Reject non-empty AWS profiles and normalize empty values away."""
        if not isinstance(aws_profile := secret_values.get("AWS_PROFILE"), str):
            return
        if aws_profile.strip():
            raise TracecatCredentialsError(
                "AWS_PROFILE is not supported in protected secret "
                f"'{secret_name}'. "
                "Use AWS_ROLE_ARN or static/session credentials instead."
            )
        secret_values.pop("AWS_PROFILE", None)

    def _get_role_arn(
        self, secret_values: dict[str, Any], secret_name: str
    ) -> str | None:
        """Return a validated AWS role ARN or None when no role should be assumed."""
        if not isinstance(role_arn := secret_values.get("AWS_ROLE_ARN"), str):
            return None
        if not (role_arn := role_arn.strip()):
            secret_values.pop("AWS_ROLE_ARN", None)
            return None
        if not self.ROLE_ARN_PATTERN.match(role_arn):
            raise TracecatCredentialsError(
                f"Invalid AWS role ARN format in secret '{secret_name}': {role_arn}"
            )
        return role_arn

    def _get_external_id(self, secrets: Mapping[str, Any]) -> str:
        """Return the runtime AWS external ID required for AssumeRole."""
        external_id = secrets.get(_AWS_EXTERNAL_ID_SECRET_KEY)
        if not isinstance(external_id, str) or not external_id:
            raise TracecatCredentialsError(
                "Missing runtime AWS external ID for AssumeRole. "
                "Ensure the workspace has a valid workspace ID."
            )
        return external_id

    def _get_role_session_name(self, secret_values: dict[str, Any]) -> str | None:
        """Return a normalized AWS role session name override, if provided."""
        if not isinstance(
            aws_role_session_name := secret_values.get("AWS_ROLE_SESSION_NAME"),
            str,
        ):
            return None
        if not (aws_role_session_name := aws_role_session_name.strip()):
            return None
        return aws_role_session_name

    def _apply_credentials(
        self, secret_values: dict[str, Any], creds: dict[str, str]
    ) -> None:
        """Replace role-based AWS config with temporary session credentials."""
        secret_values.pop("AWS_ROLE_ARN", None)
        secret_values.pop("AWS_PROFILE", None)
        secret_values["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
        secret_values["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
        secret_values["AWS_SESSION_TOKEN"] = creds["SessionToken"]

    async def preprocess(
        self,
        *,
        secrets: Mapping[str, Any],
        role: Role,
        run_context: RunContext,
    ) -> dict[str, Any]:
        """Project protected AWS secrets into env-ready session credentials."""
        projected_secrets = copy.deepcopy(dict(secrets))

        for secret_name in self.PROTECTED_SECRET_NAMES:
            if not (
                secret_values := self._get_secret_values(projected_secrets, secret_name)
            ):
                continue

            self._validate_aws_profile(secret_values, secret_name)

            if not (role_arn := self._get_role_arn(secret_values, secret_name)):
                continue

            if role.workspace_id is None:
                raise ValueError(
                    "workspace_id is required for AWS secret preprocessing"
                )

            creds = await _assume_role_via_irsa(
                role_arn=role_arn,
                external_id=self._get_external_id(secrets),
                workspace_id=str(role.workspace_id),
                run_id=str(run_context.wf_run_id),
                role_session_name=self._get_role_session_name(secret_values),
            )
            self._apply_credentials(secret_values, creds)

        return projected_secrets


def _collect_mask_values(secret_sources: Sequence[Mapping[str, Any]]) -> set[str]:
    """Collect all string-like secret values that should be masked in outputs."""
    mask_values: set[str] = set()
    for secret_source in secret_sources:
        for _, secret_value in traverse_leaves(secret_source):
            if secret_value is None:
                continue
            secret_str = str(secret_value)
            if len(secret_str) > 1:
                mask_values.add(secret_str)
            if isinstance(secret_value, str) and len(secret_value) > 1:
                mask_values.add(secret_value)
    return mask_values


_DEFAULT_SECRET_PREPROCESSORS: tuple[SecretPreprocessor, ...] = (
    AwsAssumeRoleSecretPreprocessor(),
)


async def project_secret_env(
    *,
    secrets: dict[str, Any],
    role: Role,
    run_context: RunContext,
    preprocessors: Sequence[SecretPreprocessor] = _DEFAULT_SECRET_PREPROCESSORS,
) -> SecretEnvProjection:
    """Build the env-ready secret view for runtime injection."""
    projected_secrets: dict[str, Any] = secrets
    for preprocessor in preprocessors:
        if preprocessor.matches(projected_secrets):
            projected_secrets = await preprocessor.preprocess(
                secrets=projected_secrets,
                role=role,
                run_context=run_context,
            )

    env = secrets_manager.flatten_secrets(projected_secrets)
    sources = (
        (secrets, projected_secrets) if projected_secrets is not secrets else (secrets,)
    )
    return SecretEnvProjection(env=env, mask_values=_collect_mask_values(sources))
