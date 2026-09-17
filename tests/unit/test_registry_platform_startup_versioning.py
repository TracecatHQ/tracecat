"""Tests for platform registry startup version comparison."""

from __future__ import annotations

import pytest

from tracecat.db.models import PlatformRegistryVersion


@pytest.mark.parametrize(
    ("current_version", "target_version", "expected"),
    [
        ("1.0.0-beta.47", "1.0.0-beta.48-rc.6", False),
        ("1.0.0-beta.48-rc.5", "1.0.0-beta.48-rc.6", False),
        ("1.0.0-beta.48-rc.6", "1.0.0-beta.48-rc.5", True),
        ("1.0.0-beta.48-rc.6", "1.0.0-beta.48", False),
        ("1.0.0-beta.48", "1.0.0-beta.48-rc.6", True),
        ("1.0.0-beta.52-rc.22", "1.0.0-beta.52-rc.22.post1", False),
        ("1.0.0-beta.52-rc.22.post1", "1.0.0-beta.52-rc.22", True),
        ("1.0.0-beta.52-rc.22.post1", "1.0.0-beta.52-rc.22.post1", False),
        ("1.0.0-beta.52-rc.22", "1.0.0-beta.52-rc.22.post0", False),
        ("1.0.0-beta.52-rc.22.post0", "1.0.0-beta.52-rc.22", True),
        ("1.0.0-beta.52-rc.22.post1", "1.0.0-beta.52-rc.22.post2", False),
        ("1.0.0-beta.52-rc.22.post10", "1.0.0-beta.52-rc.22.post2", True),
        ("1.0.0-beta.52-rc.22.post1", "1.0.0-beta.52-rc.23", False),
        ("1.0.0-beta.52-rc.23", "1.0.0-beta.52-rc.22.post1", True),
        ("1.0.0-beta.52-rc.22.post1", "1.0.0-beta.52", False),
        ("1.0.0-beta.52", "1.0.0-beta.52-rc.22.post1", True),
        ("1.0.0-beta.52.post1", "1.0.0-beta.52-rc.22.post1", True),
    ],
)
def test_is_downgrade_handles_temporary_beta_rc_release_tags(
    current_version: str,
    target_version: str,
    expected: bool,
) -> None:
    """Test downgrade checks for the temporary stacked beta/rc release format."""
    from tracecat.registry.sync.jobs import _is_downgrade

    current = PlatformRegistryVersion(
        version=current_version,
        manifest={"version": "1.0", "actions": {}},
        tarball_uri="s3://test/current.tar.gz",
    )

    assert _is_downgrade(current, target_version) is expected
