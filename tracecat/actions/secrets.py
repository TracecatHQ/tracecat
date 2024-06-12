"""Unified access to different secrets managers.

Supported sources:

- Tracecat config file (~/.tracecat/config.toml)
- AWS Secrets Manager
- HashiCorp Vault (WIP)

Note: `.tracecat` directory and `config.toml` file needs to be created manually for now.
"""

import os
import tomllib
from pathlib import Path

import boto3
import orjson


def _get_aws_secret_arn(secret_id: str) -> str:
    # Get account ID and region associated with current session
    account_id = os.environ["TRACECAT__AWS_ACCOUNT_ID"]
    region = os.environ["TRACECAT__AWS_DEFAULT_REGION"]
    # Specify ARN
    arn = f"arn:aws:secretsmanager:{region}:{account_id}:secret:{secret_id}"
    return arn


def _load_from_aws_secrets_manager(secret_name: str) -> dict[str, str]:
    """Load vendor secret from AWS Secrets Manager."""
    arn = _get_aws_secret_arn(secret_name)
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=arn)
    secret = response["SecretString"]
    return orjson.loads(secret)


def _load_from_config_file(secret_name: str) -> dict[str, str]:
    with open(Path.home() / ".tracecat" / "config.toml", "rb") as f:
        config = tomllib.load(f)

    if "secret" not in config:
        raise KeyError("No secrets found in config file")

    secrets = config["secrets"]
    if secret_name not in secrets:
        raise KeyError(f"No secret found for secret: {secret_name}")

    return secrets[secret_name]


def get_secret(vendor: str, secret_name: str | None = None) -> dict[str, str]:
    secret_name = secret_name or vendor
    secret_provider = os.environ.get("TRACECAT__SECRET_PROVIDER", "local")

    match secret_provider:
        case "local":
            secret = _load_from_config_file(secret_name)
        case "aws":
            secret = _load_from_aws_secrets_manager(secret_name)
        case _:
            raise ValueError(f"Unknown secret provider: {secret_provider}")

    return secret
