"""Tests for the static AI SPM control catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from tracecat_ee.spm.exceptions import SpmControlCatalogError
from tracecat_ee.spm.service import (
    get_control,
    get_control_catalog,
    load_control_catalog_from_directory,
)
from tracecat_ee.spm.taxonomy import get_inventory_taxonomy
from tracecat_ee.spm.types import (
    SpmHarness,
    SpmInventoryItemType,
    SpmInventoryRelationshipType,
    SpmInventorySourceType,
)


def test_inventory_taxonomy_covers_supported_claude_enums() -> None:
    taxonomy = get_inventory_taxonomy()
    claude_taxonomy = taxonomy.harnesses[SpmHarness.CLAUDE_CODE]

    assert set(claude_taxonomy.item_types) == set(SpmInventoryItemType)
    assert set(claude_taxonomy.source_types) == set(SpmInventorySourceType)
    assert claude_taxonomy.relationship_types == set(SpmInventoryRelationshipType)
    assert {
        source_type
        for binding in claude_taxonomy.bindings
        for source_type in binding.source_types
    } == set(SpmInventorySourceType)
    assert all(
        entry.display_value == entry.key
        for entry in [
            *claude_taxonomy.item_types.values(),
            *claude_taxonomy.source_types.values(),
        ]
    )


def test_inventory_taxonomy_includes_claude_plugin_bom_components() -> None:
    taxonomy = get_inventory_taxonomy()
    claude_taxonomy = taxonomy.harnesses[SpmHarness.CLAUDE_CODE]

    assert {
        SpmInventoryItemType.COMMAND,
        SpmInventoryItemType.LSP_SERVER,
        SpmInventoryItemType.MONITOR,
        SpmInventoryItemType.BINARY,
        SpmInventoryItemType.PLUGIN_SETTINGS,
        SpmInventoryItemType.OUTPUT_STYLE,
        SpmInventoryItemType.THEME,
        SpmInventoryItemType.SUBAGENT,
    }.issubset(claude_taxonomy.item_types)
    assert claude_taxonomy.relationship_types == {SpmInventoryRelationshipType.DEFINES}


def test_builtin_spm_control_catalog_contains_required_v1_controls() -> None:
    control_keys = {control.key for control in get_control_catalog()}

    assert "claude.mcp_server.approved" in control_keys
    assert "claude.mcp_server.vulnerability_ok" in control_keys
    assert "claude.mcp_server.reputation_ok" in control_keys
    assert "claude.hook.risk_ok" in control_keys
    assert "claude.skill.risk_ok" in control_keys
    assert "claude.instruction_file.language_english" in control_keys
    assert "claude.instruction_file.obfuscation_absent" in control_keys
    assert "claude.instruction_file.external_indicators_reputation_ok" in control_keys


def test_builtin_spm_control_catalog_uses_uuid_identity_with_readable_keys() -> None:
    controls = get_control_catalog()

    assert all(str(control.id) != control.key for control in controls)
    assert len({control.id for control in controls}) == len(controls)
    assert len({control.key for control in controls}) == len(controls)


def test_builtin_spm_control_catalog_keeps_claude_instruction_file_taxonomy() -> None:
    instruction_controls = [
        control
        for control in get_control_catalog()
        if control.item_type == SpmInventoryItemType.INSTRUCTION_FILE
    ]

    assert instruction_controls
    assert {
        source for control in instruction_controls for source in control.source_types
    } == {
        SpmInventorySourceType.CLAUDE_MD,
        SpmInventorySourceType.CLAUDE_LOCAL_MD,
    }


def test_builtin_spm_control_catalog_omits_agents_md_for_claude_v1() -> None:
    assert all(
        SpmInventorySourceType.AGENTS_MD not in control.source_types
        for control in get_control_catalog()
    )


def test_get_control_returns_expected_manifest_by_key_and_uuid() -> None:
    control = get_control("claude.mcp_server.approved")

    assert control is not None
    assert control.item_type == SpmInventoryItemType.MCP_SERVER
    assert get_control(control.id) == control


def test_load_control_catalog_rejects_missing_check_file(tmp_path: Path) -> None:
    manifest_path = tmp_path / "missing-check.yml"
    manifest_path.write_text(_manifest("test.missing_check"), encoding="utf-8")

    with pytest.raises(SpmControlCatalogError) as exc_info:
        load_control_catalog_from_directory(tmp_path)

    assert exc_info.value.code == "spm_control_check_missing"


def test_load_control_catalog_rejects_duplicate_key(tmp_path: Path) -> None:
    for index in range(2):
        manifest_path = tmp_path / f"duplicate-{index}.yml"
        manifest_path.write_text(
            _manifest("test.duplicate_key", control_id_suffix=index),
            encoding="utf-8",
        )
        (tmp_path / f"duplicate-{index}.py").write_text(
            _check_body(),
            encoding="utf-8",
        )

    with pytest.raises(SpmControlCatalogError) as exc_info:
        load_control_catalog_from_directory(tmp_path)

    assert exc_info.value.code == "spm_control_ref_duplicate"


def test_load_control_catalog_rejects_unknown_action_key(tmp_path: Path) -> None:
    manifest_path = tmp_path / "unknown-action.yml"
    manifest_path.write_text(
        _manifest("test.unknown_action", action="not_registered"),
        encoding="utf-8",
    )
    (tmp_path / "unknown-action.py").write_text(_check_body(), encoding="utf-8")

    with pytest.raises(SpmControlCatalogError) as exc_info:
        load_control_catalog_from_directory(tmp_path)

    assert exc_info.value.code == "spm_control_manifest_invalid"


def _manifest(
    key: str,
    *,
    action: str = "disable_mcp_server",
    control_id_suffix: int = 0,
) -> str:
    return "\n".join(
        [
            f"id: 00000000-0000-4000-8000-{control_id_suffix:012d}",
            f"key: {key}",
            "aliases: []",
            'revision: "1"',
            "title: Test Control",
            "description: Test manifest.",
            "harness: claude_code",
            "item_type: mcp_server",
            "severity: low",
            f"action: {action}",
        ]
    )


def _check_body() -> str:
    return "\n".join(
        [
            "from tracecat_ee.spm.schemas import SpmControlResult",
            "",
            "",
            "def check(ctx):",
            "    return SpmControlResult(failed=False, summary='ok')",
        ]
    )
