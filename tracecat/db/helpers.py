from __future__ import annotations

import asyncio

from tracecat.auth import AuthenticatedAPIClient, Role
from tracecat.db.schemas import Secret


async def batch_get_secrets(role: Role, secret_names: list[str]) -> list[Secret]:
    """Retrieve secrets from the secrets API."""

    async with AuthenticatedAPIClient(role=role) as client:
        # NOTE(perf): This is not really batched - room for improvement
        secret_responses = await asyncio.gather(
            *[client.get(f"/secrets/{secret_name}") for secret_name in secret_names]
        )
        return [
            Secret.model_validate_json(secret_bytes.content)
            for secret_bytes in secret_responses
        ]


def format_secrets_as_json(secrets: list[Secret]) -> dict[str, str]:
    """Format secrets as a dict."""
    secret_dict = {}
    for secret in secrets:
        secret_dict[secret.name] = {kv.key: kv.value for kv in secret.keys}
    return secret_dict
