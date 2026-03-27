"""Regression coverage for the committed platform catalog snapshot."""

from __future__ import annotations

from pathlib import Path

import orjson

from tracecat.agent.builtin_catalog import (
    PLATFORM_CATALOG_PATH,
    PLATFORM_CATALOG_RELATIVE_PATH,
    PLATFORM_CATALOG_SCHEMA_VERSION,
    get_builtin_catalog_metadata,
    get_builtin_catalog_models,
)


def test_platform_catalog_snapshot_exists_and_is_valid_json() -> None:
    assert (
        PLATFORM_CATALOG_PATH.resolve()
        == Path(PLATFORM_CATALOG_RELATIVE_PATH).resolve()
    )
    payload = orjson.loads(PLATFORM_CATALOG_PATH.read_bytes())

    assert payload["version"] == PLATFORM_CATALOG_SCHEMA_VERSION
    assert payload["metadata"]["upstream"]["litellm_version"] == "1.81.13"
    assert isinstance(payload["models"], dict)
    assert payload["models"]


def test_platform_catalog_snapshot_covers_tracecat_first_class_providers() -> None:
    payload = orjson.loads(PLATFORM_CATALOG_PATH.read_bytes())
    providers = {
        item["provider"]
        for item in payload["models"].values()
        if isinstance(item, dict) and isinstance(item.get("provider"), str)
    }

    assert providers == {
        "anthropic",
        "azure_ai",
        "azure_openai",
        "bedrock",
        "gemini",
        "openai",
        "vertex_ai",
    }


def test_platform_catalog_metadata_exposes_snapshot_revision() -> None:
    metadata = get_builtin_catalog_metadata()

    assert metadata["catalog_version"] == PLATFORM_CATALOG_SCHEMA_VERSION
    assert metadata["catalog_path"] == str(PLATFORM_CATALOG_RELATIVE_PATH)
    assert isinstance(metadata["catalog_sha256"], str)
    assert len(metadata["catalog_sha256"]) == 64


def test_platform_catalog_uuid_derivation_is_stable() -> None:
    first = get_builtin_catalog_models()
    second = get_builtin_catalog_models()

    assert [row.agent_catalog_id for row in first] == [
        row.agent_catalog_id for row in second
    ]


def test_platform_catalog_snapshot_contains_only_surfaced_models() -> None:
    payload = orjson.loads(PLATFORM_CATALOG_PATH.read_bytes())
    rows = get_builtin_catalog_models()

    assert len(rows) == len(payload["models"])


def test_platform_catalog_exposes_normalized_model_names_per_provider() -> None:
    rows = get_builtin_catalog_models()
    identities = {(row.model_provider, row.model_id) for row in rows}

    assert ("openai", "gpt-5") in identities
    assert ("openai", "gpt-5.4") in identities
    assert ("azure_openai", "gpt-5") in identities
    assert ("azure_openai", "gpt-5.4-mini") in identities
    assert ("anthropic", "claude-sonnet-4-6") in identities
    assert ("gemini", "gemini-3-flash-preview") in identities
    assert ("gemini", "gemini-3.1-pro-preview") in identities
    assert ("vertex_ai", "gemini-3-flash-preview") in identities
    assert ("vertex_ai", "gemini-3.1-pro-preview") in identities
    assert ("bedrock", "amazon.nova-2-pro-preview-20251202-v1:0") in identities
    assert ("azure_ai", "grok-4") in identities
    assert all(
        ":" not in row.model_id for row in rows if row.model_provider != "bedrock"
    )
