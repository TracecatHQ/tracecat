"""Tests for OSS tier default entitlement resolution."""

from tracecat.tiers.defaults import (
    get_default_tier_entitlements_env,
    get_legacy_feature_flags_env,
    resolve_default_tier_entitlement_enables,
    resolve_oss_default_entitlements,
)


def test_resolve_oss_default_entitlements_fresh_install() -> None:
    entitlements = resolve_oss_default_entitlements(None)

    assert entitlements.custom_registry is True
    assert entitlements.git_sync is False
    assert entitlements.agent_addons is False
    assert entitlements.case_addons is False
    assert entitlements.watchtower is False


def test_resolve_oss_default_entitlements_maps_legacy_feature_flags() -> None:
    entitlements = resolve_oss_default_entitlements(
        "git-sync,agent-approvals,case-durations"
    )

    assert entitlements.custom_registry is True
    assert entitlements.git_sync is True
    assert entitlements.agent_addons is True
    assert entitlements.case_addons is True
    assert entitlements.watchtower is False


def test_resolve_oss_default_entitlements_normalizes_and_ignores_unknown() -> None:
    entitlements = resolve_oss_default_entitlements(
        " GIT_SYNC , unknown-flag , CASE_TRIGGERS "
    )

    assert entitlements.custom_registry is True
    assert entitlements.git_sync is True
    assert entitlements.agent_addons is False
    assert entitlements.case_addons is True
    assert entitlements.watchtower is False


def test_get_legacy_feature_flags_env_reads_tracecat_feature_flags(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRACECAT__FEATURE_FLAGS", "git-sync")

    assert get_legacy_feature_flags_env() == "git-sync"


def test_get_default_tier_entitlements_env_reads_tracecat_dev_default_tier_entitlements(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRACECAT__DEV_DEFAULT_TIER_ENTITLEMENTS", "all")

    assert get_default_tier_entitlements_env() == "all"


def test_resolve_default_tier_entitlement_enables_supports_all() -> None:
    entitlements = resolve_default_tier_entitlement_enables("all")

    assert entitlements == {
        "custom_registry": True,
        "git_sync": True,
        "agent_addons": True,
        "case_addons": True,
        "rbac_addons": True,
        "watchtower": True,
    }


def test_resolve_default_tier_entitlement_enables_only_accepts_canonical_keys() -> None:
    entitlements = resolve_default_tier_entitlement_enables(
        "agent-addons,case-addons,rbac-addons,watchtower,agent_presets,unknown"
    )

    assert entitlements == {
        "agent_addons": True,
        "case_addons": True,
        "rbac_addons": True,
        "watchtower": True,
    }
