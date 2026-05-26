"""Seed Integration catalog rows from API/OAuth provider metadata.

This is invoked after the catalog migration. It is idempotent —
running again upserts metadata for known namespaces without duplicating.

MCP servers are intentionally excluded from this catalog — they back
agents, not Tracecat Actions, and live on the MCP page with their own
backing table (``MCPIntegration``).

Static credentials remain in Secret and are projected through the catalog API.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    Integration,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.integrations.enums import IntegrationSource
from tracecat.integrations.providers import all_providers
from tracecat.logger import logger
from tracecat.registry.versions.schemas import RegistryVersionManifest

# Providers ship as separate classes per OAuth grant flow (delegated vs
# client credentials) but share a base namespace. We want one catalog row
# per service, not per grant flow, so we strip the variant suffix from the
# display name before inserting.
_VARIANT_SUFFIX_RE = re.compile(
    r"\s*\((?:Delegated|Service account|Service principal)\)\s*$",
    re.IGNORECASE,
)


def _canonical_display_name(name: str) -> str:
    return _VARIANT_SUFFIX_RE.sub("", name).strip()


def _humanize_namespace(namespace: str) -> str:
    """Turn a snake_case secret name into a display name.

    ``abuseipdb`` → ``Abuseipdb`` (best-effort; provider-shipped rows
    overwrite this with a friendlier label via the OAuth metadata seed).
    """
    return " ".join(part.capitalize() for part in namespace.split("_"))


async def _platform_secret_definitions(
    session: AsyncSession,
) -> dict[str, int]:
    """Aggregate secret namespaces declared by platform-shipped actions.

    Returns a mapping of secret name → action_count. OAuth-only secrets
    (``RegistryOAuthSecret`` or names ending in ``_oauth``) are skipped
    since those are already covered by the provider seed.
    """
    stmt = (
        select(PlatformRegistryVersion.manifest)
        .join(
            PlatformRegistryRepository,
            PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
        )
        .where(
            PlatformRegistryRepository.current_version_id == PlatformRegistryVersion.id,
        )
    )
    result = await session.execute(stmt)
    counts: dict[str, int] = {}
    for (manifest_data,) in result.all():
        manifest = RegistryVersionManifest.model_validate(manifest_data)
        for action_name, manifest_action in manifest.actions.items():
            if not manifest_action.secrets:
                continue
            for secret in manifest_action.secrets:
                name = getattr(secret, "name", None)
                if not name or name.endswith("_oauth"):
                    continue
                counts[name] = counts.get(name, 0) + 1
            _ = action_name  # silence loop var warning
    return counts


async def seed_platform_integrations(session: AsyncSession) -> int:
    """Upsert one Integration row per base service (collapsing OAuth variants).

    Returns the number of rows created.
    """
    existing_namespaces = set(
        (
            await session.execute(
                select(Integration.namespace).where(Integration.workspace_id.is_(None))
            )
        ).scalars()
    )

    # Track namespaces we've already queued during this seed pass so two
    # provider classes sharing a namespace (delegated + service principal)
    # only insert one row.
    queued_namespaces: set[str] = set()
    created = 0
    for provider_cls in all_providers():
        metadata = getattr(provider_cls, "metadata", None)
        if metadata is None:
            continue

        namespace = metadata.id
        # MCP providers belong on the MCP page, not in the action catalog.
        if namespace.endswith("_mcp"):
            continue
        if namespace in existing_namespaces or namespace in queued_namespaces:
            continue

        row = Integration(
            workspace_id=None,
            namespace=namespace,
            display_name=_canonical_display_name(metadata.name),
            description=metadata.description,
            icon_url=None,
            source=IntegrationSource.PLATFORM,
        )
        session.add(row)
        queued_namespaces.add(namespace)
        created += 1

    # Pull every secret namespace declared by platform-shipped actions and
    # surface them as catalog rows. This is what makes the page show
    # API-key-backed integrations (abusech, anthropic, ...) without the
    # user having to dig into the Secrets surface.
    secret_counts = await _platform_secret_definitions(session)
    for namespace, action_count in sorted(secret_counts.items()):
        if namespace in existing_namespaces or namespace in queued_namespaces:
            continue
        description = (
            f"Used by {action_count} action{'s' if action_count != 1 else ''}."
        )
        row = Integration(
            workspace_id=None,
            namespace=namespace,
            display_name=_humanize_namespace(namespace),
            description=description,
            icon_url=None,
            source=IntegrationSource.PLATFORM,
        )
        session.add(row)
        queued_namespaces.add(namespace)
        created += 1

    await session.flush()
    logger.info("Seeded platform integrations", created=created)
    return created
