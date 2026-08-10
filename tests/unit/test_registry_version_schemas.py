from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from tracecat.registry.versions.schemas import (
    RegistryVersionCreate,
    RegistryVersionManifest,
)


def _registry_version_create(*, artifact_hash: str | None) -> RegistryVersionCreate:
    return RegistryVersionCreate(
        repository_id=uuid.uuid4(),
        version="1.0.0",
        manifest=RegistryVersionManifest(),
        tarball_uri="s3://registry-artifacts/path/site-packages.squashfs",
        artifact_hash=artifact_hash,
    )


def test_registry_version_create_accepts_sha256_artifact_hash() -> None:
    version = _registry_version_create(artifact_hash="a" * 64)

    assert version.artifact_hash == "a" * 64


@pytest.mark.parametrize(
    "artifact_hash",
    [
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "not-a-sha256",
    ],
)
def test_registry_version_create_rejects_invalid_artifact_hash(
    artifact_hash: str,
) -> None:
    with pytest.raises(ValidationError):
        _registry_version_create(artifact_hash=artifact_hash)
