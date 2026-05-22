"""Helpers for release-built registry sync metadata."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.artifact_keys import get_artifact_s3_prefix
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.versions.schemas import RegistryVersionManifest

PREBUILT_MANIFEST_FILENAME = "manifest.json"


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
