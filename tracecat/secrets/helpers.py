from __future__ import annotations

import asyncio

from tracecat import db
from tracecat.auth import AuthenticatedAPIClient, Role


async def batch_get_secrets(role: Role, secret_names: list[str]) -> list[db.Secret]:
    """Retrieve secrets from the secrets API."""

    async with AuthenticatedAPIClient(role=role, http2=True) as client:
        # NOTE(perf): This is not really batched - room for improvement
        secret_responses = await asyncio.gather(
            *[client.get(f"/secrets/{secret_name}") for secret_name in secret_names]
        )
        return [
            db.Secret.model_validate_json(secret_bytes.content)
            for secret_bytes in secret_responses
        ]
