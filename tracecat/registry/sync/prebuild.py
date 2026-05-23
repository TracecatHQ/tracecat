"""Build release-carried builtin registry metadata."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import tracecat_registry

from tracecat import config
from tracecat.registry.artifact_keys import get_artifact_local_dir
from tracecat.registry.constants import (
    DEFAULT_REGISTRY_ORIGIN,
    PLATFORM_REGISTRY_NAMESPACE,
)
from tracecat.registry.sync.artifact import build_builtin_registry_artifact
from tracecat.registry.sync.entrypoint import load_and_serialize_actions
from tracecat.registry.sync.prebuilt import (
    PrebuiltRegistryArtifactMetadata,
    write_prebuilt_registry_artifact_metadata,
    write_prebuilt_registry_manifest,
)
from tracecat.registry.versions.schemas import RegistryVersionManifest

PREBUILD_REPOSITORY_ID = UUID("00000000-0000-4000-8000-000000000000")


async def prebuild_builtin_registry_manifest(output_root: Path | None = None) -> Path:
    """Build the builtin registry manifest into the deterministic local layout."""
    root = output_root or Path(config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR)
    output_dir = get_artifact_local_dir(
        root=root,
        organization_id=PLATFORM_REGISTRY_NAMESPACE,
        repository_origin=DEFAULT_REGISTRY_ORIGIN,
        version=tracecat_registry.__version__,
    )
    sync_result = await load_and_serialize_actions(
        origin=DEFAULT_REGISTRY_ORIGIN,
        repository_id=PREBUILD_REPOSITORY_ID,
        validate=True,
    )
    if sync_result.validation_errors:
        error_count = sum(
            len(errors) for errors in sync_result.validation_errors.values()
        )
        raise RuntimeError(
            f"Builtin registry prebuild found {error_count} validation error(s)"
        )
    manifest = RegistryVersionManifest.from_actions(sync_result.actions)
    write_prebuilt_registry_manifest(artifact_dir=output_dir, manifest=manifest)
    artifact = await build_builtin_registry_artifact(output_dir=output_dir)
    write_prebuilt_registry_artifact_metadata(
        artifact_dir=output_dir,
        metadata=PrebuiltRegistryArtifactMetadata(
            artifact_hash=artifact.content_hash,
            artifact_size_bytes=artifact.artifact_size_bytes,
        ),
    )
    artifact.squashfs_path.unlink(missing_ok=True)
    return output_dir


def main() -> None:
    """Build builtin registry metadata for the current image."""
    output_dir = asyncio.run(prebuild_builtin_registry_manifest())
    print(f"Prebuilt builtin registry manifest in {output_dir}")


if __name__ == "__main__":
    main()
