from __future__ import annotations

import uuid
from collections.abc import Sequence
from types import TracebackType
from typing import Any

import pytest

from tracecat.exceptions import RegistryValidationError
from tracecat.executor.service import (
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    registry_manifest_fingerprint,
)

type ArtifactRow = tuple[str, str, str | None, str | None, dict[str, Any]]


class _FakeResult:
    def __init__(self, rows: Sequence[ArtifactRow]) -> None:
        self._rows = rows

    def all(self) -> Sequence[ArtifactRow]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: Sequence[ArtifactRow]) -> None:
        self._rows = rows

    async def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._rows)


class _FakeSessionManager:
    def __init__(self, rows: Sequence[ArtifactRow]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession(self._rows)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def _manifest_dict(manifest: RegistryVersionManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json")


def _artifact_row(
    *,
    origin: str,
    version: str,
    artifact_hash: str | None,
    manifest: RegistryVersionManifest,
) -> ArtifactRow:
    return (
        origin,
        version,
        f"s3://bucket/{uuid.uuid4().hex}/site-packages.squashfs",
        artifact_hash,
        _manifest_dict(manifest),
    )


def _patch_artifact_lookup_session(
    monkeypatch: pytest.MonkeyPatch,
    rows: Sequence[ArtifactRow],
) -> None:
    monkeypatch.setattr(
        "tracecat.executor.service.get_async_session_bypass_rls_context_manager",
        lambda: _FakeSessionManager(rows),
    )


@pytest.mark.anyio
async def test_artifact_hash_lock_rejects_current_db_hash_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "git+ssh://github.com/example/custom.git"
    version = "v1"
    locked_hash = "a" * 64
    current_db_hash = "b" * 64
    _patch_artifact_lookup_session(
        monkeypatch,
        [
            _artifact_row(
                origin=origin,
                version=version,
                artifact_hash=current_db_hash,
                manifest=RegistryVersionManifest(),
            )
        ],
    )

    with pytest.raises(
        RegistryValidationError,
        match="Locked registry artifact fingerprint mismatch",
    ):
        await get_registry_artifacts_for_lock(
            origins={origin: version},
            organization_id=uuid.uuid4(),
            origin_fingerprints={origin: locked_hash},
        )


@pytest.mark.anyio
async def test_artifact_hash_lock_preserves_matching_locked_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "git+ssh://github.com/example/custom.git"
    version = "v1"
    locked_hash = "a" * 64
    _patch_artifact_lookup_session(
        monkeypatch,
        [
            _artifact_row(
                origin=origin,
                version=version,
                artifact_hash=locked_hash,
                manifest=RegistryVersionManifest(),
            )
        ],
    )

    artifacts = await get_registry_artifacts_for_lock(
        origins={origin: version},
        organization_id=uuid.uuid4(),
        origin_fingerprints={origin: locked_hash},
    )

    assert artifacts == [
        RegistryArtifactsContext(
            origin=origin,
            version=version,
            artifact_uri=artifacts[0].artifact_uri,
            artifact_hash=locked_hash,
        )
    ]


@pytest.mark.anyio
async def test_manifest_fingerprint_lock_allows_current_artifact_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "git+ssh://github.com/example/custom.git"
    version = "v1"
    artifact_hash = "b" * 64
    manifest = RegistryVersionManifest()
    manifest_fingerprint = registry_manifest_fingerprint(manifest)
    _patch_artifact_lookup_session(
        monkeypatch,
        [
            _artifact_row(
                origin=origin,
                version=version,
                artifact_hash=artifact_hash,
                manifest=manifest,
            )
        ],
    )

    artifacts = await get_registry_artifacts_for_lock(
        origins={origin: version},
        organization_id=uuid.uuid4(),
        origin_fingerprints={origin: manifest_fingerprint},
    )

    assert artifacts == [
        RegistryArtifactsContext(
            origin=origin,
            version=version,
            artifact_uri=artifacts[0].artifact_uri,
            artifact_hash=artifact_hash,
        )
    ]


@pytest.mark.anyio
async def test_lookup_without_lock_fingerprint_uses_current_artifact_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "git+ssh://github.com/example/custom.git"
    version = "v1"
    artifact_hash = "c" * 64
    _patch_artifact_lookup_session(
        monkeypatch,
        [
            _artifact_row(
                origin=origin,
                version=version,
                artifact_hash=artifact_hash,
                manifest=RegistryVersionManifest(),
            )
        ],
    )

    artifacts = await get_registry_artifacts_for_lock(
        origins={origin: version},
        organization_id=uuid.uuid4(),
    )

    assert artifacts == [
        RegistryArtifactsContext(
            origin=origin,
            version=version,
            artifact_uri=artifacts[0].artifact_uri,
            artifact_hash=artifact_hash,
        )
    ]
