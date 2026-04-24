"""Load platform catalog on startup."""

import importlib.resources as resources
import logging
from typing import cast

import orjson

from tracecat.agent.catalog.service import AgentCatalogService, PlatformCatalogEntry
from tracecat.db.engine import get_async_session_bypass_rls_context_manager

logger = logging.getLogger(__name__)


def get_platform_catalog_models() -> list[PlatformCatalogEntry]:
    """Load and parse platform_catalog.json.

    Returns a list of ``PlatformCatalogEntry`` rows (model_provider,
    model_name, metadata). Empty list if the bundled file is missing or
    invalid so API startup stays resilient to packaging issues.
    """
    try:
        catalog_data = orjson.loads(
            resources.files("tracecat.agent")
            .joinpath("platform_catalog.json")
            .read_bytes()
        )
    except (FileNotFoundError, IsADirectoryError, orjson.JSONDecodeError, OSError):
        return []

    models = catalog_data.get("models", [])
    return [
        cast(PlatformCatalogEntry, m)
        for m in models
        if m.get("model_provider") and m.get("model_name")
    ]


async def load_platform_catalog_on_startup() -> None:
    """Load the platform catalog from JSON on API startup (non-blocking).

    Idempotent: ``AgentCatalogService.upsert_platform_catalog`` runs a single
    ``INSERT ... ON CONFLICT DO UPDATE`` so every platform model lands in one
    statement and one transaction. Edits to ``platform_catalog.json`` propagate
    on next startup without any manual reset. Runs as a fire-and-forget task
    so API boot is not blocked on DB roundtrips.
    """
    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            service = AgentCatalogService(session=session)
            upserted = await service.upsert_platform_catalog(
                get_platform_catalog_models()
            )
            logger.info(f"Platform catalog loaded: {upserted} models upserted")
    except Exception as e:
        logger.error(f"Unexpected error loading platform catalog: {e}")
