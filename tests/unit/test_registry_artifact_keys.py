"""Tests for the shared registry artifact key/URI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracecat.registry.artifact_keys import (
    get_artifact_local_dir,
    get_artifact_s3_key,
    get_artifact_s3_prefix,
    get_squashfs_artifact_key,
    parse_s3_uri,
)


class TestParseS3Uri:
    def test_valid_uri(self) -> None:
        bucket, key = parse_s3_uri("s3://my-bucket/path/to/file.squashfs")
        assert bucket == "my-bucket"
        assert key == "path/to/file.squashfs"

    def test_nested_path(self) -> None:
        bucket, key = parse_s3_uri("s3://bucket/a/b/c/file.tar.gz")
        assert bucket == "bucket"
        assert key == "a/b/c/file.tar.gz"

    @pytest.mark.parametrize(
        "uri",
        [
            "https://bucket/key",
            "s3://bucket",
            "s3:///key",
        ],
    )
    def test_invalid_uri(self, uri: str) -> None:
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            parse_s3_uri(uri)


def test_get_artifact_s3_prefix_returns_directory() -> None:
    prefix = get_artifact_s3_prefix(
        organization_id="platform",
        repository_origin="tracecat_registry",
        version="1.0.0",
    )
    assert prefix == "platform/tarball-venvs/tracecat_registry/1.0.0"


def test_get_artifact_s3_key_returns_squashfs_path() -> None:
    key = get_artifact_s3_key(
        organization_id="org-abc",
        repository_origin="git+ssh://git@github.com/Acme/registry.git",
        version="1.2.3",
    )
    assert key.startswith("org-abc/tarball-venvs/")
    assert key.endswith("/site-packages.squashfs")


def test_get_artifact_s3_key_avoids_long_origin_collisions() -> None:
    origin_prefix = "git+ssh://git@github.com/acme/" + ("shared-" * 20)

    first_key = get_artifact_s3_key(
        organization_id="org-abc",
        repository_origin=f"{origin_prefix}-first.git",
        version="1.2.3",
    )
    second_key = get_artifact_s3_key(
        organization_id="org-abc",
        repository_origin=f"{origin_prefix}-second.git",
        version="1.2.3",
    )

    assert first_key != second_key
    assert len(first_key.split("/")[2]) == 100
    assert len(second_key.split("/")[2]) == 100
    assert first_key.endswith("/site-packages.squashfs")
    assert second_key.endswith("/site-packages.squashfs")


def test_get_squashfs_artifact_key_normalizes_inputs() -> None:
    base = "platform/tarball-venvs/test/1.0.0/site-packages"
    assert get_squashfs_artifact_key(f"{base}.tar.gz") == f"{base}.squashfs"
    assert get_squashfs_artifact_key(f"{base}.squashfs") == f"{base}.squashfs"
    # Anything else gets a trailing .squashfs (defensive default for unknown
    # legacy keys).
    assert get_squashfs_artifact_key("custom/key") == "custom/key.squashfs"


def test_get_artifact_local_dir_matches_s3_prefix() -> None:
    local_dir = get_artifact_local_dir(
        root=Path("/prebuilt"),
        organization_id="platform",
        repository_origin="tracecat_registry",
        version="1.0.0",
    )
    assert local_dir == Path("/prebuilt/platform/tarball-venvs/tracecat_registry/1.0.0")
