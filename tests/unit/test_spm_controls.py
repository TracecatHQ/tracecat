"""Tests for the static AI SPM control catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from tracecat_ee.spm.controls import get_control, get_control_catalog
from tracecat_ee.spm.controls.registry import load_control_catalog_from_directory
from tracecat_ee.spm.types import SpmAssetClass, SpmAssetType


def test_builtin_spm_control_catalog_contains_required_v1_controls() -> None:
    control_ids = {control.id for control in get_control_catalog()}

    assert "claude.mcp_server.approved" in control_ids
    assert "claude.mcp_server.vulnerability_ok" in control_ids
    assert "claude.mcp_server.reputation_ok" in control_ids
    assert "claude.hook.risk_ok" in control_ids
    assert "claude.skill.risk_ok" in control_ids
    assert "claude.instruction_file.language_english" in control_ids
    assert "claude.instruction_file.obfuscation_absent" in control_ids
    assert "claude.instruction_file.external_indicators_reputation_ok" in control_ids


def test_builtin_spm_control_catalog_keeps_claude_instruction_file_taxonomy() -> None:
    instruction_controls = [
        control
        for control in get_control_catalog()
        if control.asset_class == SpmAssetClass.INSTRUCTION_FILE
    ]

    assert instruction_controls
    assert {control.asset_type for control in instruction_controls} == {
        SpmAssetType.CLAUDE_MD
    }


def test_builtin_spm_control_catalog_omits_agents_md_for_claude_v1() -> None:
    assert all(
        control.asset_type != SpmAssetType.AGENTS_MD
        for control in get_control_catalog()
    )


def test_get_control_returns_expected_manifest() -> None:
    control = get_control("claude.mcp_server.approved")

    assert control is not None
    assert control.asset_class == SpmAssetClass.MCP_SERVER
    assert control.asset_type == SpmAssetType.MCP_SERVER


def test_load_control_catalog_rejects_unknown_check_key(tmp_path: Path) -> None:
    manifest_path = tmp_path / "unknown-check.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "id: test.unknown_check",
                'revision: "1"',
                "title: Test Unknown Check",
                "description: Test manifest.",
                "harness: claude_code",
                "asset_class: mcp_server",
                "asset_type: mcp_server",
                "severity: low",
                "check: not_registered",
                "action: disable_mcp_server",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown SPM control check key"):
        load_control_catalog_from_directory(tmp_path)


def test_load_control_catalog_rejects_unknown_action_key(tmp_path: Path) -> None:
    manifest_path = tmp_path / "unknown-action.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "id: test.unknown_action",
                'revision: "1"',
                "title: Test Unknown Action",
                "description: Test manifest.",
                "harness: claude_code",
                "asset_class: mcp_server",
                "asset_type: mcp_server",
                "severity: low",
                "check: mcp_server_approved",
                "action: not_registered",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown SPM control action key"):
        load_control_catalog_from_directory(tmp_path)
