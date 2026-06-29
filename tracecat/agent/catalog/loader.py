"""Load platform catalog on startup."""

import importlib.resources as resources
import logging
from typing import Any, cast

import orjson
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracecat.agent.catalog.service import AgentCatalogService, PlatformCatalogEntry
from tracecat.db.engine import get_async_session_bypass_rls_context_manager

logger = logging.getLogger(__name__)


class RawPlatformCatalogEntry(BaseModel):
    """Model row from bundled platform_catalog.json (trust boundary)."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_platform_catalog_models() -> list[PlatformCatalogEntry]:
    """Load and parse platform_catalog.json.

    Returns a list of ``PlatformCatalogEntry`` rows (model_provider,
    model_name, metadata); malformed rows are skipped. Empty list if the
    bundled file is missing or invalid so API startup stays resilient to
    packaging issues.
    """
    try:
        catalog_data = orjson.loads(
            resources.files("tracecat.agent")
            .joinpath("platform_catalog.json")
            .read_bytes()
        )
    except (FileNotFoundError, IsADirectoryError, orjson.JSONDecodeError, OSError):
        return []

    if not isinstance(catalog_data, dict):
        return []
    catalog_doc = cast(dict[str, Any], catalog_data)

    raw_models = catalog_doc.get("models", [])
    if not isinstance(raw_models, list):
        return []
    models = cast(list[Any], raw_models)

    valid_models: list[PlatformCatalogEntry] = []
    for model in models:
        try:
            raw = RawPlatformCatalogEntry.model_validate(model)
        except ValidationError:
            continue
        valid_models.append(
            PlatformCatalogEntry(
                model_provider=raw.model_provider,
                model_name=raw.model_name,
                metadata=raw.metadata,
            )
        )
    return valid_models


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
