from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from tracecat import config


def get_tracecat_aws_account_id() -> str:
    """Return the AWS account ID used by Tracecat workloads."""
    if account_id := config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID:
        return account_id
    raise ValueError("TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID is not configured")


def get_tracecat_aws_principal_arn() -> str:
    """Return the dedicated AWS principal ARN used for customer AssumeRole."""
    if principal_arn := config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN:
        return principal_arn
    raise ValueError("TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN is not configured")


def build_workspace_external_id(workspace_id: UUID | str) -> str:
    """Build a stable workspace-scoped External ID for AWS AssumeRole."""
    if not (secret := config.TRACECAT__AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET):
        raise ValueError(
            "TRACECAT__AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET is not configured"
        )

    digest = hmac.new(
        secret.encode("utf-8"),
        str(workspace_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"tracecat-ws-{digest[:32]}"
