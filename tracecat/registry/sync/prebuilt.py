"""Helpers for release-built registry sync metadata."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.sync.tarball import get_tarball_venv_s3_key
from tracecat.registry.versions.schemas import RegistryVersionManifest

PREBUILT_MANIFEST_FILENAME = "manifest.json"


def _prebuilt_root() -> Path:
    return Path(config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR)


def _builtin_artifact_key(
    *,
    origin: str,
    target_version: str | None,
    storage_namespace: str,
) -> str | None:
    if origin != DEFAULT_REGISTRY_ORIGIN or target_version is None:
        return None
    return get_tarball_venv_s3_key(
        organization_id=storage_namespace,
        repository_origin=origin,
        version=target_version,
    )


def get_prebuilt_registry_manifest_path(*, root: Path, key: str) -> Path:
    """Return the manifest path for a deterministic builtin artifact key."""
    return root / Path(key).parent / PREBUILT_MANIFEST_FILENAME


def write_prebuilt_registry_manifest(
    *,
    artifact_dir: Path,
    manifest: RegistryVersionManifest,
) -> Path:
    """Write a release-built registry manifest."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / PREBUILT_MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
    return manifest_path


def load_prebuilt_registry_manifest(
    *,
    root: Path,
    key: str,
) -> RegistryVersionManifest | None:
    """Load a release-built registry manifest when present."""
    manifest_path = get_prebuilt_registry_manifest_path(root=root, key=key)
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
    key = _builtin_artifact_key(
        origin=origin,
        target_version=target_version,
        storage_namespace=storage_namespace,
    )
    if key is None:
        return None

    return load_prebuilt_registry_manifest(
        root=_prebuilt_root(),
        key=key,
    )
