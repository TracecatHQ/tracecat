"""Read the platform catalog file bundled with Tracecat."""

import importlib.resources as resources
from collections.abc import Mapping
from functools import cache
from typing import Any, cast

import orjson
from pydantic import ValidationError

from tracecat.agent.catalog.types import ModelKey, ModelLifecycle, RawRetiredModel
from tracecat.logger import logger


def read_platform_catalog_document() -> dict[str, Any]:
    """Return the parsed bundled catalog, or an empty document if unreadable."""
    try:
        document = orjson.loads(
            resources.files("tracecat.agent")
            .joinpath("platform_catalog.json")
            .read_bytes()
        )
    except (orjson.JSONDecodeError, OSError) as e:
        logger.warning("Bundled platform catalog is unreadable", error=str(e))
        return {}
    if not isinstance(document, dict):
        logger.warning("Bundled platform catalog is not a JSON object")
        return {}
    return cast(dict[str, Any], document)


def parse_retired_models(
    document: Mapping[str, Any],
) -> dict[ModelKey, ModelLifecycle]:
    """Parse the ``retired`` section; malformed entries are skipped."""
    raw_entries = document.get("retired", [])
    if not isinstance(raw_entries, list):
        return {}
    retired: dict[ModelKey, ModelLifecycle] = {}
    for raw in cast(list[Any], raw_entries):
        try:
            entry = RawRetiredModel.model_validate(raw)
        except ValidationError:
            continue
        retired[ModelKey(entry.model_provider, entry.model_name)] = entry.lifecycle
    return retired


@cache
def get_retired_platform_models() -> Mapping[ModelKey, ModelLifecycle]:
    """Retired platform models removed from the catalog, keyed by identity."""
    return parse_retired_models(read_platform_catalog_document())
