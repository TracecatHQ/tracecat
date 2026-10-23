"""Tests for platform model lifecycle parsing and bundled seed data."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from tracecat.agent.catalog.bundled import (
    parse_retired_models,
    read_platform_catalog_document,
)
from tracecat.agent.catalog.lifecycle import read_model_lifecycle
from tracecat.agent.catalog.loader import get_platform_catalog_models
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.agent.catalog.types import ModelLifecycle

# Capabilities an agent run can depend on: tools, structured output, thinking,
# and file inputs. A replacement must keep every one its retired model had.
_RUN_CAPABILITIES = (
    "supports_function_calling",
    "supports_tool_choice",
    "supports_response_schema",
    "supports_reasoning",
    "supports_vision",
    "supports_pdf_input",
)


def test_status_turns_retired_on_retirement_date() -> None:
    lifecycle = ModelLifecycle(
        deprecated_on=date(2026, 4, 22),
        retires_on=date(2026, 7, 23),
    )

    assert lifecycle.status_on(date(2026, 7, 22)) == "deprecated"
    assert lifecycle.status_on(date(2026, 7, 23)) == "retired"


@pytest.mark.parametrize(
    "metadata",
    [None, {}, {"lifecycle": None}, {"lifecycle": {"retires_on": "not-a-date"}}],
)
def test_read_model_lifecycle_ignores_missing_or_malformed(
    metadata: dict[str, object] | None,
) -> None:
    assert read_model_lifecycle(metadata) is None


def test_catalog_read_exposes_lifecycle_for_platform_rows_only() -> None:
    metadata = {
        "lifecycle": {
            "retires_on": "2000-01-01",
            "replacement": "claude-sonnet-5-5",
        }
    }
    row = {
        "id": uuid.uuid4(),
        "custom_provider_id": None,
        "model_provider": "anthropic",
        "model_name": "claude-4-sonnet-20250514",
        "model_metadata": metadata,
    }

    platform = AgentCatalogRead.model_validate({**row, "organization_id": None})
    org_owned = AgentCatalogRead.model_validate(
        {**row, "organization_id": uuid.uuid4()}
    )

    assert platform.lifecycle is not None
    assert platform.lifecycle.status == "retired"
    assert platform.lifecycle.replacement == "claude-sonnet-5-5"
    assert org_owned.lifecycle is None


def test_bundled_lifecycles_point_at_compatible_current_replacements() -> None:
    entries = {
        (entry.model_provider, entry.model_name): entry.metadata
        for entry in get_platform_catalog_models()
    }
    seeded = 0
    for (provider, model_name), metadata in entries.items():
        if "lifecycle" not in metadata:
            continue
        lifecycle = read_model_lifecycle(metadata)
        assert lifecycle is not None, f"{model_name}: malformed lifecycle"
        seeded += 1
        if lifecycle.deprecated_on is not None:
            assert lifecycle.deprecated_on < lifecycle.retires_on, model_name
        if lifecycle.replacement is None:
            continue

        replacement = entries.get((provider, lifecycle.replacement))
        assert replacement is not None, (
            f"{model_name}: replacement {lifecycle.replacement} is not in the catalog"
        )
        assert read_model_lifecycle(replacement) is None, (
            f"{model_name}: replacement {lifecycle.replacement} is itself deprecated"
        )
        missing = [
            capability
            for capability in _RUN_CAPABILITIES
            if metadata.get(capability) is True
            and replacement.get(capability) is not True
        ]
        assert not missing, f"{model_name} -> {lifecycle.replacement}: {missing}"
    assert seeded > 0


def test_bundled_retired_models_point_at_current_replacements() -> None:
    current = {
        (entry.model_provider, entry.model_name): entry.metadata
        for entry in get_platform_catalog_models()
    }
    document = read_platform_catalog_document()
    retired = parse_retired_models(document)

    # Every raw entry parses, so none is silently dropped.
    assert len(retired) == len(document.get("retired", []))
    for key, lifecycle in retired.items():
        assert key not in current, f"{key.model_name}: still in models"
        assert lifecycle.status_on() == "retired", key.model_name
        target = lifecycle.replacement_key(key.model_provider)
        if target is None:
            continue
        # A retired replacement must be repointed to its own successor.
        replacement = current.get(target)
        assert replacement is not None, f"{key.model_name}: {target.model_name} gone"
        assert read_model_lifecycle(replacement) is None, (
            f"{key.model_name}: {target.model_name} is itself deprecated"
        )
