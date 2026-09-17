"""Tests for platform registry startup version comparison."""

from __future__ import annotations

import pytest

from tracecat.db.models import PlatformRegistryVersion


def _version(version: str) -> PlatformRegistryVersion:
    return PlatformRegistryVersion(
        version=version,
        manifest={"version": "1.0", "actions": {}},
        tarball_uri="s3://test/current.tar.gz",
    )


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
def test_is_downgrade_handles_legacy_chained_release_tags(
    current_version: str,
    target_version: str,
    expected: bool,
) -> None:
    """Legacy pre-1.0 chained tags still order as `<base>-rc.N` < `<base>`.

    Production databases may still hold these tags, so the parser keeps
    accepting them even though the convention is retired.
    """
    from tracecat.registry.sync.jobs import _is_downgrade

    assert _is_downgrade(_version(current_version), target_version) is expected


@pytest.mark.parametrize(
    ("current_version", "target_version", "expected"),
    [
        # A later number in the same prerelease series moves forward.
        ("1.1.0-alpha.2", "1.1.0-alpha.3", False),
        ("1.1.0-alpha.3", "1.1.0-alpha.2", True),
        # A hotfix sorts after the prerelease it is cut from.
        ("1.1.0-alpha.2", "1.1.0-alpha.2.1", False),
        ("1.1.0-alpha.2.1", "1.1.0-alpha.2", True),
        # Hotfixes order among themselves.
        ("1.1.0-alpha.2.1", "1.1.0-alpha.2.6", False),
        ("1.1.0-alpha.2.6", "1.1.0-alpha.2.1", True),
        # A hotfix still sorts before the next prerelease number.
        ("1.1.0-alpha.2.6", "1.1.0-alpha.3", False),
        ("1.1.0-alpha.3", "1.1.0-alpha.2.6", True),
        # alpha precedes beta.
        ("1.1.0-alpha.9", "1.1.0-beta.1", False),
        ("1.1.0-beta.1", "1.1.0-alpha.9", True),
        # A prerelease precedes its stable release.
        ("1.1.0-beta.3", "1.1.0", False),
        ("1.1.0", "1.1.0-beta.3", True),
        # A stable release precedes the next minor's first alpha.
        ("1.0.0", "1.1.0-alpha.1", False),
        ("1.1.0-alpha.1", "1.0.0", True),
        # Legacy chained tags still order below the new convention.
        ("1.0.0-beta.52-rc.24", "1.1.0-alpha.1", False),
        ("1.1.0-alpha.1", "1.0.0-beta.52-rc.24", True),
    ],
)
def test_is_downgrade_handles_release_tags(
    current_version: str,
    target_version: str,
    expected: bool,
) -> None:
    """Release tags order as `alpha.N < alpha.N.M < alpha.N+1 < beta.1 < stable`.

    The base of a prerelease is the next stable version, so any `X.Y.Z` stable
    tag sorts below every prerelease of a later `X.Y.Z`.
    """
    from tracecat.registry.sync.jobs import _is_downgrade

    assert _is_downgrade(_version(current_version), target_version) is expected
