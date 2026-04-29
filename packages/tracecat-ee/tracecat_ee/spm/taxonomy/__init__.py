"""Manifest-backed Agent SPM inventory taxonomy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tracecat_ee.spm.types import (
    SpmHarness,
    SpmInventoryItemType,
    SpmInventoryRelationshipType,
    SpmInventorySourceType,
)

_SNAKE_CASE_RE = re.compile(r"^[a-z0-9_]+$")
_SOURCE_LOCATION_BASENAMES: dict[SpmInventorySourceType, str] = {
    SpmInventorySourceType.SETTINGS_JSON: "settings.json",
    SpmInventorySourceType.SETTINGS_LOCAL_JSON: "settings.local.json",
    SpmInventorySourceType.CLAUDE_JSON: ".claude.json",
    SpmInventorySourceType.HOOKS_JSON: "hooks.json",
    SpmInventorySourceType.MCP_JSON: ".mcp.json",
    SpmInventorySourceType.CLAUDE_MD: "CLAUDE.md",
    SpmInventorySourceType.CLAUDE_LOCAL_MD: "CLAUDE.local.md",
    SpmInventorySourceType.AGENTS_MD: "AGENTS.md",
    SpmInventorySourceType.PLUGIN_MANIFEST: "plugin.json",
}
_FRONTMATTER_SOURCE_TYPES = frozenset(
    {
        SpmInventorySourceType.SKILL_FRONTMATTER,
        SpmInventorySourceType.AGENT_FRONTMATTER,
    }
)


@dataclass(frozen=True, slots=True)
class SpmInventoryTaxonomyEntry:
    key: str
    display_value: str
    icon_key: str
    description: str
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class SpmInventoryTaxonomyBinding:
    item_type: SpmInventoryItemType
    source_types: frozenset[SpmInventorySourceType]
    enforcement: str


@dataclass(frozen=True, slots=True)
class SpmInventoryHarnessTaxonomy:
    item_types: dict[SpmInventoryItemType, SpmInventoryTaxonomyEntry]
    source_types: dict[SpmInventorySourceType, SpmInventoryTaxonomyEntry]
    bindings: tuple[SpmInventoryTaxonomyBinding, ...]
    relationship_types: frozenset[SpmInventoryRelationshipType]


@dataclass(frozen=True, slots=True)
class SpmInventoryTaxonomy:
    version: int
    harnesses: dict[SpmHarness, SpmInventoryHarnessTaxonomy]


def taxonomy_manifest_path() -> Path:
    return Path(__file__).with_name("inventory.yml")


@lru_cache(maxsize=1)
def get_inventory_taxonomy() -> SpmInventoryTaxonomy:
    """Load the Agent SPM inventory taxonomy manifest."""
    with taxonomy_manifest_path().open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("SPM inventory taxonomy manifest must be an object.")
    return _parse_taxonomy(raw)


def inventory_taxonomy_as_dict() -> dict[str, Any]:
    taxonomy = get_inventory_taxonomy()
    return {
        "version": taxonomy.version,
        "harnesses": {
            harness.value: {
                "item_types": [
                    _entry_dict(entry) for entry in harness_taxonomy.item_types.values()
                ],
                "source_types": [
                    _entry_dict(entry)
                    for entry in harness_taxonomy.source_types.values()
                ],
                "bindings": [
                    {
                        "item_type": binding.item_type.value,
                        "source_types": [
                            source_type.value
                            for source_type in sorted(
                                binding.source_types, key=lambda value: value.value
                            )
                        ],
                        "enforcement": binding.enforcement,
                    }
                    for binding in harness_taxonomy.bindings
                ],
                "relationship_types": [
                    relationship_type.value
                    for relationship_type in sorted(
                        harness_taxonomy.relationship_types,
                        key=lambda value: value.value,
                    )
                ],
            }
            for harness, harness_taxonomy in taxonomy.harnesses.items()
        },
    }


def validate_inventory_binding(
    *,
    harness: SpmHarness,
    item_type: SpmInventoryItemType,
    source_type: SpmInventorySourceType,
    source_location: str,
) -> None:
    taxonomy = get_inventory_taxonomy().harnesses.get(harness)
    if taxonomy is None:
        raise ValueError(f"Unsupported SPM harness: {harness.value}")
    if item_type not in taxonomy.item_types:
        raise ValueError(f"Unsupported SPM inventory item type: {item_type.value}")
    if source_type not in taxonomy.source_types:
        raise ValueError(f"Unsupported SPM inventory source type: {source_type.value}")
    for binding in taxonomy.bindings:
        if binding.item_type == item_type and source_type in binding.source_types:
            if source_location:
                _validate_source_location(
                    source_type=source_type,
                    source_location=source_location,
                )
            return
    raise ValueError(
        "Invalid SPM inventory item/source binding: "
        f"{item_type.value}/{source_type.value}"
    )


def validate_control_target(
    *,
    harness: SpmHarness,
    item_type: SpmInventoryItemType,
    source_types: list[SpmInventorySourceType],
) -> None:
    taxonomy = get_inventory_taxonomy().harnesses.get(harness)
    if taxonomy is None:
        raise ValueError(f"Unsupported SPM harness: {harness.value}")
    if item_type not in taxonomy.item_types:
        raise ValueError(f"Unsupported SPM inventory item type: {item_type.value}")
    for source_type in source_types:
        validate_inventory_binding(
            harness=harness,
            item_type=item_type,
            source_type=source_type,
            source_location="",
        )


def _parse_taxonomy(raw: dict[str, Any]) -> SpmInventoryTaxonomy:
    version = raw.get("version")
    harnesses = raw.get("harnesses")
    if not isinstance(version, int) or not isinstance(harnesses, dict):
        raise ValueError("SPM inventory taxonomy requires version and harnesses.")

    taxonomy = SpmInventoryTaxonomy(
        version=version,
        harnesses={
            SpmHarness(harness): _parse_harness_taxonomy(raw_harness)
            for harness, raw_harness in harnesses.items()
        },
    )
    _validate_taxonomy(taxonomy)
    return taxonomy


def _parse_harness_taxonomy(raw: Any) -> SpmInventoryHarnessTaxonomy:
    if not isinstance(raw, dict):
        raise ValueError("SPM harness taxonomy must be an object.")
    item_types = {
        SpmInventoryItemType(key): _parse_entry(key, value)
        for key, value in _dict(raw.get("item_types")).items()
    }
    source_types = {
        SpmInventorySourceType(key): _parse_entry(key, value)
        for key, value in _dict(raw.get("source_types")).items()
    }
    bindings = tuple(_parse_binding(binding) for binding in _list(raw.get("bindings")))
    relationship_types = frozenset(
        SpmInventoryRelationshipType(value)
        for value in _list(raw.get("relationship_types"))
        if isinstance(value, str)
    )

    return SpmInventoryHarnessTaxonomy(
        item_types=item_types,
        source_types=source_types,
        bindings=bindings,
        relationship_types=relationship_types,
    )


def _parse_entry(key: str, raw: Any) -> SpmInventoryTaxonomyEntry:
    value = _dict(raw)
    return SpmInventoryTaxonomyEntry(
        key=key,
        display_value=str(value.get("display_value") or key),
        icon_key=str(value.get("icon_key") or ""),
        description=str(value.get("description") or ""),
        kind=str(value["kind"]) if "kind" in value else None,
    )


def _parse_binding(raw: Any) -> SpmInventoryTaxonomyBinding:
    value = _dict(raw)
    source_types = _list(value.get("source_types"))
    return SpmInventoryTaxonomyBinding(
        item_type=SpmInventoryItemType(value["item_type"]),
        source_types=frozenset(
            SpmInventorySourceType(source_type) for source_type in source_types
        ),
        enforcement=str(value.get("enforcement") or ""),
    )


def _entry_dict(entry: SpmInventoryTaxonomyEntry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "display_value": entry.display_value,
        "icon_key": entry.icon_key,
        "description": entry.description,
        "kind": entry.kind,
    }


def _validate_taxonomy(taxonomy: SpmInventoryTaxonomy) -> None:
    expected_harnesses = set(SpmHarness)
    if set(taxonomy.harnesses) != expected_harnesses:
        missing = sorted(
            harness.value for harness in expected_harnesses - set(taxonomy.harnesses)
        )
        extra = sorted(
            harness.value for harness in set(taxonomy.harnesses) - expected_harnesses
        )
        raise ValueError(
            "SPM inventory taxonomy harness coverage mismatch: "
            f"missing={missing}, extra={extra}"
        )

    for harness, harness_taxonomy in taxonomy.harnesses.items():
        _validate_harness_taxonomy(harness=harness, taxonomy=harness_taxonomy)


def _validate_harness_taxonomy(
    *,
    harness: SpmHarness,
    taxonomy: SpmInventoryHarnessTaxonomy,
) -> None:
    expected_item_types = set(SpmInventoryItemType)
    expected_source_types = set(SpmInventorySourceType)
    expected_relationship_types = set(SpmInventoryRelationshipType)

    if set(taxonomy.item_types) != expected_item_types:
        _raise_coverage_error(
            harness=harness,
            label="item_types",
            actual={item_type.value for item_type in taxonomy.item_types},
            expected={item_type.value for item_type in expected_item_types},
        )
    if set(taxonomy.source_types) != expected_source_types:
        _raise_coverage_error(
            harness=harness,
            label="source_types",
            actual={source_type.value for source_type in taxonomy.source_types},
            expected={source_type.value for source_type in expected_source_types},
        )
    if taxonomy.relationship_types != expected_relationship_types:
        _raise_coverage_error(
            harness=harness,
            label="relationship_types",
            actual={
                relationship_type.value
                for relationship_type in taxonomy.relationship_types
            },
            expected={
                relationship_type.value
                for relationship_type in expected_relationship_types
            },
        )

    for entry in [*taxonomy.item_types.values(), *taxonomy.source_types.values()]:
        _validate_snake_case(entry.display_value, label=f"{entry.key}.display_value")
        if entry.description:
            _validate_snake_case(entry.description, label=f"{entry.key}.description")

    seen_pairs: set[tuple[SpmInventoryItemType, SpmInventorySourceType]] = set()
    bound_item_types: set[SpmInventoryItemType] = set()
    bound_source_types: set[SpmInventorySourceType] = set()
    for binding in taxonomy.bindings:
        if binding.item_type not in taxonomy.item_types:
            raise ValueError(
                "SPM inventory taxonomy binding references undeclared item type: "
                f"{binding.item_type.value}"
            )
        _validate_snake_case(binding.enforcement, label="binding.enforcement")
        bound_item_types.add(binding.item_type)

        for source_type in binding.source_types:
            if source_type not in taxonomy.source_types:
                raise ValueError(
                    "SPM inventory taxonomy binding references undeclared source "
                    f"type: {source_type.value}"
                )
            pair = (binding.item_type, source_type)
            if pair in seen_pairs:
                raise ValueError(
                    "SPM inventory taxonomy declares duplicate binding: "
                    f"{binding.item_type.value}/{source_type.value}"
                )
            seen_pairs.add(pair)
            bound_source_types.add(source_type)

    if bound_item_types != expected_item_types:
        _raise_coverage_error(
            harness=harness,
            label="bound_item_types",
            actual={item_type.value for item_type in bound_item_types},
            expected={item_type.value for item_type in expected_item_types},
        )
    if bound_source_types != expected_source_types:
        _raise_coverage_error(
            harness=harness,
            label="bound_source_types",
            actual={source_type.value for source_type in bound_source_types},
            expected={source_type.value for source_type in expected_source_types},
        )


def _validate_source_location(
    *,
    source_type: SpmInventorySourceType,
    source_location: str,
) -> None:
    if expected_name := _SOURCE_LOCATION_BASENAMES.get(source_type):
        if Path(source_location).name != expected_name:
            raise ValueError(
                "Invalid SPM inventory source location for "
                f"{source_type.value}: expected basename {expected_name!r}"
            )
    elif source_type in _FRONTMATTER_SOURCE_TYPES:
        if Path(source_location).suffix.lower() != ".md":
            raise ValueError(
                "Invalid SPM inventory source location for "
                f"{source_type.value}: expected markdown file"
            )


def _validate_snake_case(value: str, *, label: str) -> None:
    if not _SNAKE_CASE_RE.fullmatch(value):
        raise ValueError(f"SPM inventory taxonomy {label} must be snake_case.")


def _raise_coverage_error(
    *,
    harness: SpmHarness,
    label: str,
    actual: set[str],
    expected: set[str],
) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise ValueError(
        f"SPM inventory taxonomy {harness.value}.{label} coverage mismatch: "
        f"missing={missing}, extra={extra}"
    )


def _dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _list(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []
