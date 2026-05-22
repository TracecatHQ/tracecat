"""Helpers for release-built registry sync artifacts."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.sync.tarball import (
    get_tarball_venv_s3_key,
    get_tarball_venv_s3_uri,
    tarball_venv_artifacts_exist,
    upload_prebuilt_tarball_venv,
)
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.storage import blob

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
    """Return the manifest path for a prebuilt tarball artifact key."""
    return root / Path(key).parent / PREBUILT_MANIFEST_FILENAME


def write_prebuilt_registry_manifest(
    *,
    artifact_dir: Path,
    manifest: RegistryVersionManifest,
) -> Path:
    """Write a registry manifest next to release-built execution artifacts."""
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


async def reuse_or_upload_prebuilt_builtin_artifacts(
    *,
    origin: str,
    target_version: str | None,
    storage_namespace: str,
) -> str | None:
    """Return a builtin tarball URI by reusing remote or uploading image artifacts."""
    key = _builtin_artifact_key(
        origin=origin,
        target_version=target_version,
        storage_namespace=storage_namespace,
    )
    if key is None:
        return None

    bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY
    await blob.ensure_bucket_exists(bucket)
    require_squashfs = config.TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED
    if await tarball_venv_artifacts_exist(
        key=key,
        bucket=bucket,
        require_squashfs=require_squashfs,
    ):
        return get_tarball_venv_s3_uri(bucket=bucket, key=key)

    return await upload_prebuilt_tarball_venv(
        root=_prebuilt_root(),
        key=key,
        bucket=bucket,
        require_squashfs=require_squashfs,
    )
