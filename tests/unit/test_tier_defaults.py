"""Tests for OSS tier default entitlement resolution."""

from importlib.metadata import EntryPoint

import pytest

from tracecat.agent.backends.constants import AGENT_BACKEND_ENTRY_POINT_GROUP
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.defaults import (
    get_legacy_feature_flags_env,
    has_installed_agent_backend,
    resolve_oss_default_entitlements,
)

_FALSE_BY_DEFAULT = {
    "git_sync": False,
    "agent_addons": False,
    "case_addons": False,
    "service_accounts": False,
    "multi_workspace": False,
    "watchtower": False,
    "agent_channels": False,
    "semantic_search": False,
    "forms": False,
    "tunnels": False,
    "agent_runtime_preview": False,
}


@pytest.mark.parametrize(
    ("flags", "enabled"),
    [
        pytest.param(None, set(), id="fresh_install"),
        pytest.param(
            "git-sync,agent-approvals,case-durations",
            {"git_sync", "agent_addons", "case_addons"},
            id="legacy_feature_flags",
        ),
        pytest.param(
            " GIT_SYNC , unknown-flag , CASE_TRIGGERS ",
            {"git_sync", "case_addons"},
            id="normalizes_and_ignores_unknown",
        ),
    ],
)
def test_resolve_oss_default_entitlements(flags: str | None, enabled: set[str]) -> None:
    entitlements = resolve_oss_default_entitlements(flags)

    expected = {
        **_FALSE_BY_DEFAULT,
        **dict.fromkeys(enabled, True),
        "custom_registry": True,
        "workspace_chat": True,
    }
    assert {name: getattr(entitlements, name) for name in expected} == expected


def test_resolve_oss_default_entitlements_maps_legacy_agent_channels_flag() -> None:
    entitlements = resolve_oss_default_entitlements("AGENT_CHANNELS")

    assert entitlements.agent_channels is True
    assert entitlements.agent_addons is False


def test_resolve_oss_default_entitlements_maps_forms_flag() -> None:
    entitlements = resolve_oss_default_entitlements("git-sync, forms")

    assert entitlements.forms is True
    assert entitlements.semantic_search is False


@pytest.mark.parametrize("flags", [None, "git-sync"], ids=["fresh", "legacy_flags"])
def test_installed_agent_backend_enables_agent_runtime_preview(
    flags: str | None,
) -> None:
    entitlements = resolve_oss_default_entitlements(flags, agent_backend_installed=True)

    assert entitlements.agent_runtime_preview is True
    assert entitlements.agent_addons is False


def test_agent_runtime_flag_no_longer_enables_agent_runtime_preview() -> None:
    entitlements = resolve_oss_default_entitlements("agent-runtime")

    assert entitlements.agent_runtime_preview is False


def test_has_installed_agent_backend_reads_entry_point_group(monkeypatch) -> None:
    groups: list[str] = []

    def fake_entry_points(*, group: str) -> list[EntryPoint]:
        groups.append(group)
        return [EntryPoint(name="extra", value="pkg:factory", group=group)]

    monkeypatch.setattr(tier_defaults, "entry_points", fake_entry_points)

    assert has_installed_agent_backend() is True
    assert groups == [AGENT_BACKEND_ENTRY_POINT_GROUP]

    monkeypatch.setattr(tier_defaults, "entry_points", lambda *, group: [])

    assert has_installed_agent_backend() is False


def test_resolve_oss_default_entitlements_normalizes_and_ignores_unknown() -> None:
    entitlements = resolve_oss_default_entitlements(
        " GIT_SYNC , unknown-flag , CASE_TRIGGERS "
    )

    assert entitlements.custom_registry is True
    assert entitlements.git_sync is True
    assert entitlements.agent_addons is False
    assert entitlements.case_addons is True
    assert entitlements.service_accounts is False
    assert entitlements.workspace_chat is True
    assert entitlements.multi_workspace is False
    assert entitlements.watchtower is False
    assert entitlements.agent_channels is False
    assert entitlements.semantic_search is False
    assert entitlements.forms is False
    assert entitlements.tunnels is False
    assert entitlements.agent_runtime_preview is False


def test_get_legacy_feature_flags_env_reads_tracecat_feature_flags(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRACECAT__FEATURE_FLAGS", "git-sync")

    assert get_legacy_feature_flags_env() == "git-sync"
