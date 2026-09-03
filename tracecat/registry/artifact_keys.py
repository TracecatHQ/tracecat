"""S3 key and URI helpers for registry execution artifacts.

Shared by the sync side (build + upload) and the executor side (download
+ materialize) so artifact paths stay consistent across the system.

The `tarball-venvs/` S3 prefix and the `get_tarball_*` and
`get_squashfs_*` helper names are kept for URI stability with registry
versions written before the SquashFS-only migration; new artifacts under
this prefix are `.squashfs` images.
"""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

REGISTRY_ARTIFACT_S3_NAMESPACE = "tarball-venvs"
"""Historical S3 namespace for registry execution artifacts."""

REGISTRY_ARTIFACT_FILENAME = "site-packages.squashfs"
"""Filename for the SquashFS execution artifact."""

LEGACY_TARBALL_FILENAME = "site-packages.tar.gz"
"""Filename for the legacy gzip tarball artifact (read-only at runtime)."""

ORIGIN_SLUG_MAX_LENGTH = 100
ORIGIN_SLUG_HASH_LENGTH = 12


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse an `s3://bucket/key` URI into `(bucket, key)`."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _slugify_origin(origin: str) -> str:
    """Convert a repository origin to a safe slug for S3 keys."""
    normalized = (
        origin.replace("git+ssh://", "").replace("https://", "").replace("http://", "")
    )
    slug = normalized
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
    slug = re.sub(r"_+", "_", slug)
    slug = slug.strip("_")
    if len(slug) <= ORIGIN_SLUG_MAX_LENGTH:
        return slug

    digest = sha256(normalized.encode()).hexdigest()[:ORIGIN_SLUG_HASH_LENGTH]
    prefix_length = ORIGIN_SLUG_MAX_LENGTH - ORIGIN_SLUG_HASH_LENGTH - 1
    return f"{slug[:prefix_length]}_{digest}"


def get_artifact_s3_prefix(
    organization_id: str,
    repository_origin: str,
    version: str,
) -> str:
    """Return the directory-style S3 key prefix for a registry artifact."""
    return (
        f"{organization_id}/{REGISTRY_ARTIFACT_S3_NAMESPACE}/"
        f"{_slugify_origin(repository_origin)}/{version}"
    )


def get_artifact_s3_key(
    organization_id: str,
    repository_origin: str,
    version: str,
) -> str:
    """Return the SquashFS artifact S3 key for a registry version."""
    prefix = get_artifact_s3_prefix(organization_id, repository_origin, version)
    return f"{prefix}/{REGISTRY_ARTIFACT_FILENAME}"


def get_squashfs_artifact_key(artifact_key: str) -> str:
    """Return the SquashFS key for a tarball-or-SquashFS S3 key.

    Accepts legacy `.tar.gz` keys and returns the SquashFS sibling key.
    Passes already-SquashFS keys through unchanged. Used by the backfill
    flow where the database may hold either format.
    """
    if artifact_key.endswith(".squashfs"):
        return artifact_key
    if artifact_key.endswith(".tar.gz"):
        return artifact_key.removesuffix(".tar.gz") + ".squashfs"
    return f"{artifact_key}.squashfs"


def get_artifact_local_dir(
    *,
    root: Path,
    organization_id: str,
    repository_origin: str,
    version: str,
) -> Path:
    """Return the local on-disk directory matching the S3 prefix layout."""
    return root / get_artifact_s3_prefix(
        organization_id=organization_id,
        repository_origin=repository_origin,
        version=version,
    )
