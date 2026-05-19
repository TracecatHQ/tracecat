"""Tests for registry sync Temporal workflow activity behavior."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo
from tracecat.registry.sync.runner import RegistrySyncValidationError
from tracecat.registry.sync.schemas import (
    RegistryArtifactsBackfillItem,
    RegistrySyncRequest,
)
from tracecat.registry.sync.workflow import (
    backfill_registry_artifacts_activity,
    sync_registry_activity,
)


@pytest.mark.anyio
async def test_sync_registry_activity_raises_validation_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync activity should fail fast when validation errors are returned."""

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            raise RegistrySyncValidationError(
                {
                    "tools.example.action": [
                        RegistryActionValidationErrorInfo(
                            type=TemplateActionValidationErrorType.SERIALIZATION_ERROR,
                            details=["Forbidden access to os.environ"],
                            is_template=False,
                            loc_primary="tools.example.action",
                            loc_secondary=None,
                        )
                    ]
                }
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(ApplicationError, match="Registry sync validation failed"):
        await sync_registry_activity(request)


@pytest.mark.anyio
async def test_backfill_registry_artifacts_activity_skips_existing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        assert bucket == "registry-artifacts"
        assert key == "platform/tarball-venvs/test/1.0.0/site-packages.squashfs"
        return True

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.blob.file_exists",
        fake_file_exists,
    )

    version_id = uuid4()
    result = await backfill_registry_artifacts_activity(
        RegistryArtifactsBackfillItem(
            version_id=version_id,
            version="1.0.0",
            tarball_uri=(
                "s3://registry-artifacts/platform/tarball-venvs/test/1.0.0/"
                "site-packages.tar.gz"
            ),
        )
    )

    assert result.status == "exists"
    assert result.version_id == version_id


@pytest.mark.anyio
async def test_backfill_registry_artifacts_activity_creates_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded: dict[str, object] = {}

    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        assert key.endswith("site-packages.squashfs")
        assert bucket == "registry-artifacts"
        return False

    async def fake_download_tarball_venv(
        *,
        key: str,
        bucket: str,
        output_path: Path,
    ) -> Path:
        assert key.endswith("site-packages.tar.gz")
        assert bucket == "registry-artifacts"
        return output_path

    async def fake_build_squashfs_sidecar_from_tarball(
        *,
        tarball_path: Path,
        squashfs_path: Path,
        work_dir: Path,
    ) -> bool:
        assert tarball_path.name == "site-packages.tar.gz"
        assert work_dir.name == "extract"
        squashfs_path.write_bytes(b"squashfs")
        return True

    async def fake_upload_file_from_path(
        *,
        path: Path,
        key: str,
        bucket: str,
        content_type: str | None,
    ) -> None:
        kwargs = {
            "path": path,
            "key": key,
            "bucket": bucket,
            "content_type": content_type,
        }
        uploaded.update(kwargs)

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.blob.file_exists",
        fake_file_exists,
    )
    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.download_tarball_venv",
        fake_download_tarball_venv,
    )
    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.build_squashfs_sidecar_from_tarball",
        fake_build_squashfs_sidecar_from_tarball,
    )
    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.blob.upload_file_from_path",
        fake_upload_file_from_path,
    )

    result = await backfill_registry_artifacts_activity(
        RegistryArtifactsBackfillItem(
            version_id=uuid4(),
            version="1.0.0",
            tarball_uri=(
                "s3://registry-artifacts/platform/tarball-venvs/test/1.0.0/"
                "site-packages.tar.gz"
            ),
        )
    )

    assert result.status == "created"
    assert uploaded["key"] == "platform/tarball-venvs/test/1.0.0/site-packages.squashfs"
    assert uploaded["bucket"] == "registry-artifacts"
    assert uploaded["content_type"] == "application/vnd.squashfs"
