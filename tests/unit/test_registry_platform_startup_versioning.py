"""Tests for platform registry startup version comparison."""

from __future__ import annotations

import pytest

from tracecat.db.models import PlatformRegistryVersion
from tracecat.registry.sync.jobs import _is_downgrade


@pytest.mark.parametrize(
    ("current_version", "target_version", "expected"),
    [
        ("1.0.0-beta.47", "1.0.0-beta.48-rc.6", False),
        ("1.0.0-beta.48-rc.5", "1.0.0-beta.48-rc.6", False),
        ("1.0.0-beta.48-rc.6", "1.0.0-beta.48-rc.5", True),
        ("1.0.0-beta.48-rc.6", "1.0.0-beta.48", False),
        ("1.0.0-beta.48", "1.0.0-beta.48-rc.6", True),
    ],
)
def test_is_downgrade_handles_temporary_beta_rc_release_tags(
    current_version: str,
    target_version: str,
    expected: bool,
) -> None:
    """Test downgrade checks for the temporary stacked beta/rc release format."""
    current = PlatformRegistryVersion(
        version=current_version,
        manifest={"version": "1.0", "actions": {}},
        tarball_uri="s3://test/current.tar.gz",
    )

    assert _is_downgrade(current, target_version) is expected


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("1.2.0-alpha.1", "1.2.0-alpha.1.0"),
        ("1.2.0-alpha.1", "1.2.0-alpha.1.2"),
        ("1.2.0-alpha.1.2", "1.2.0-alpha.1.10"),
        ("1.2.0-alpha.1.10", "1.2.0-alpha.2"),
        ("1.2.0-alpha.1.10", "1.2.0-alpha.2.1"),
        ("1.2.0-alpha.1.2", "1.2.0-beta.0"),
        ("1.2.0-alpha.1.2", "1.2.0-beta.0-rc.1"),
        ("1.2.0-alpha.1.2", "1.2.0-rc.0"),
        ("1.2.0-alpha.1.2", "1.2.0"),
        ("1.1.0", "1.2.0-alpha.1.2"),
    ],
)
def test_is_downgrade_orders_alpha_hotfix_tags(older: str, newer: str) -> None:
    """Allow upgrades and reject downgrades across alpha hotfix boundaries."""
    older_version = PlatformRegistryVersion(version=older)
    newer_version = PlatformRegistryVersion(version=newer)

    assert _is_downgrade(older_version, newer) is False
    assert _is_downgrade(newer_version, older) is True


@pytest.mark.parametrize("current", ["1.2.0-alpha.1.2", "1.2.0a1.post2"])
def test_is_downgrade_accepts_equivalent_alpha_hotfix_versions(current: str) -> None:
    """Public and Python hotfix versions compare as the same release."""
    current_version = PlatformRegistryVersion(version=current)

    assert _is_downgrade(current_version, "1.2.0-alpha.1.2") is False
    assert _is_downgrade(current_version, "1.2.0a1.post2") is False
