"""Helpers for release-built registry sync metadata."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.artifact_keys import get_artifact_s3_prefix
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.versions.schemas import RegistryVersionManifest

PREBUILT_MANIFEST_FILENAME = "manifest.json"
PREBUILT_ARTIFACT_METADATA_FILENAME = "artifact.json"


class PrebuiltRegistryArtifactMetadata(BaseModel):
    """Release-built metadata for the builtin registry execution artifact."""

    artifact_hash: str
    artifact_size_bytes: int


def _prebuilt_root() -> Path:
    return Path(config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR)


def _builtin_artifact_prefix(
    *,
    origin: str,
    target_version: str | None,
    storage_namespace: str,
) -> str | None:
    if origin != DEFAULT_REGISTRY_ORIGIN or target_version is None:
        return None
    return get_artifact_s3_prefix(
        organization_id=storage_namespace,
        repository_origin=origin,
        version=target_version,
    )


def get_prebuilt_registry_manifest_path(*, root: Path, prefix: str) -> Path:
    """Return the manifest path for a deterministic artifact prefix."""
    return root / prefix / PREBUILT_MANIFEST_FILENAME


def get_prebuilt_registry_artifact_metadata_path(*, root: Path, prefix: str) -> Path:
    """Return the artifact metadata path for a deterministic artifact prefix."""
    return root / prefix / PREBUILT_ARTIFACT_METADATA_FILENAME


def write_prebuilt_registry_manifest(
    *,
    artifact_dir: Path,
    manifest: RegistryVersionManifest,
) -> Path:
    """Write a compact release-built registry manifest."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / PREBUILT_MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json())
    return manifest_path


def write_prebuilt_registry_artifact_metadata(
    *,
    artifact_dir: Path,
    metadata: PrebuiltRegistryArtifactMetadata,
) -> Path:
    """Write compact release-built artifact identity metadata."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = artifact_dir / PREBUILT_ARTIFACT_METADATA_FILENAME
    metadata_path.write_text(metadata.model_dump_json())
    return metadata_path


def load_prebuilt_registry_manifest(
    *,
    root: Path,
    prefix: str,
) -> RegistryVersionManifest | None:
    """Load a release-built registry manifest when present."""
    manifest_path = get_prebuilt_registry_manifest_path(root=root, prefix=prefix)
    if not manifest_path.exists():
        return None
    try:
        return RegistryVersionManifest.model_validate_json(manifest_path.read_text())
    except (OSError, ValueError, ValidationError) as e:
        logger.warning(
            "Ignoring invalid prebuilt registry manifest",
            manifest_path=str(manifest_path),
            error=str(e),
        )
        return None


def load_prebuilt_registry_artifact_metadata(
    *,
    root: Path,
    prefix: str,
) -> PrebuiltRegistryArtifactMetadata | None:
    """Load release-built artifact identity metadata when present."""
    metadata_path = get_prebuilt_registry_artifact_metadata_path(
        root=root,
        prefix=prefix,
    )
    if not metadata_path.exists():
        return None
    try:
        return PrebuiltRegistryArtifactMetadata.model_validate_json(
            metadata_path.read_text()
        )
    except (OSError, ValueError, ValidationError) as e:
        logger.warning(
            "Ignoring invalid prebuilt registry artifact metadata",
            metadata_path=str(metadata_path),
            error=str(e),
        )
        return None


def load_prebuilt_builtin_registry_manifest(
    *,
    origin: str,
    target_version: str | None,
    storage_namespace: str,
) -> RegistryVersionManifest | None:
    """Load release-built builtin registry manifest when it is available."""
    prefix = _builtin_artifact_prefix(
        origin=origin,
        target_version=target_version,
        storage_namespace=storage_namespace,
    )
    if prefix is None:
        return None

    return load_prebuilt_registry_manifest(
        root=_prebuilt_root(),
        prefix=prefix,
    )


def load_prebuilt_builtin_registry_artifact_metadata(
    *,
    origin: str,
    target_version: str | None,
    storage_namespace: str,
) -> PrebuiltRegistryArtifactMetadata | None:
    """Load release-built builtin registry artifact metadata when available."""
    prefix = _builtin_artifact_prefix(
        origin=origin,
        target_version=target_version,
        storage_namespace=storage_namespace,
    )
    if prefix is None:
        return None

    return load_prebuilt_registry_artifact_metadata(
        root=_prebuilt_root(),
        prefix=prefix,
    )
