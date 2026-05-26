"""Tests for registry sync Temporal workflow activity behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from temporalio.exceptions import ActivityError, ApplicationError

from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo
from tracecat.registry.sync.runner import (
    ActionDiscoveryError,
    RegistrySyncValidationError,
)
from tracecat.registry.sync.schemas import (
    RegistryArtifactsBackfillItem,
    RegistryArtifactsBackfillItemResult,
    RegistryArtifactsBackfillRequest,
    RegistrySyncRequest,
)
from tracecat.registry.sync.workflow import (
    RegistryArtifactsBackfillWorkflow,
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
async def test_sync_registry_activity_raises_non_retryable_error_for_content_discovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery/template-load failures should surface immediately, not retry to timeout."""

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            raise ActionDiscoveryError(
                "Failed to discover actions: Failed to load template action from "
                "/custom_actions/example_template.yml: "
                "Invalid type annotation for expected field 'items': 'list'. "
                "Lists must include an item type, e.g. 'list[str]' or 'list[Any]'.",
                non_retryable=True,
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(ApplicationError) as exc_info:
        await sync_registry_activity(request)

    exc = exc_info.value
    assert exc.type == "RegistrySyncValidationError"
    assert exc.non_retryable is True
    assert "example_template.yml" in str(exc)
    assert "list[str]" in str(exc)


@pytest.mark.anyio
async def test_sync_registry_activity_keeps_transient_discovery_errors_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient discovery failures should bubble up so Temporal can retry them."""

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            raise ActionDiscoveryError(
                "Failed to discover actions: Sync subprocess timed out after 300s"
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(ActionDiscoveryError, match="timed out"):
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
async def test_backfill_registry_artifacts_activity_accepts_existing_squashfs_uri(
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

    result = await backfill_registry_artifacts_activity(
        RegistryArtifactsBackfillItem(
            version_id=uuid4(),
            version="1.0.0",
            tarball_uri=(
                "s3://registry-artifacts/platform/tarball-venvs/test/1.0.0/"
                "site-packages.squashfs"
            ),
        )
    )

    assert result.status == "exists"


@pytest.mark.anyio
async def test_backfill_registry_artifacts_activity_skips_missing_squashfs_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        assert bucket == "registry-artifacts"
        assert key == "platform/tarball-venvs/test/1.0.0/site-packages.squashfs"
        return False

    async def fail_download_tarball_venv(**kwargs: object) -> Path:
        raise AssertionError(f"download should not be called: {kwargs}")

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.blob.file_exists",
        fake_file_exists,
    )
    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.download_tarball_venv",
        fail_download_tarball_venv,
    )

    result = await backfill_registry_artifacts_activity(
        RegistryArtifactsBackfillItem(
            version_id=uuid4(),
            version="1.0.0",
            tarball_uri=(
                "s3://registry-artifacts/platform/tarball-venvs/test/1.0.0/"
                "site-packages.squashfs"
            ),
        )
    )

    assert result.status == "skipped"
    assert result.error == (
        "SquashFS artifact is missing and no tarball source is available."
    )


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


@pytest.mark.anyio
async def test_backfill_registry_artifacts_activity_reraises_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        del key, bucket
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.blob.file_exists",
        fake_file_exists,
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await backfill_registry_artifacts_activity(
            RegistryArtifactsBackfillItem(
                version_id=uuid4(),
                version="1.0.0",
                tarball_uri=(
                    "s3://registry-artifacts/platform/tarball-venvs/test/1.0.0/"
                    "site-packages.tar.gz"
                ),
            )
        )


@pytest.mark.anyio
async def test_backfill_workflow_continues_after_item_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_version_id = uuid4()
    next_version_id = uuid4()

    async def fake_execute_activity(
        _activity: Any,
        item: RegistryArtifactsBackfillItem,
        **_kwargs: Any,
    ) -> RegistryArtifactsBackfillItemResult:
        if item.version_id == failed_version_id:
            try:
                raise RuntimeError("corrupt tarball")
            except RuntimeError as exc:
                raise ActivityError(
                    "Activity task failed",
                    scheduled_event_id=1,
                    started_event_id=2,
                    identity="test",
                    activity_type="backfill_registry_artifacts_activity",
                    activity_id="activity-id",
                    retry_state=None,
                ) from exc
        return RegistryArtifactsBackfillItemResult(
            version_id=item.version_id,
            status="created",
        )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.workflow.execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.workflow.logger",
        SimpleNamespace(
            info=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
    )

    result = await RegistryArtifactsBackfillWorkflow().run(
        RegistryArtifactsBackfillRequest(
            items=[
                RegistryArtifactsBackfillItem(
                    version_id=failed_version_id,
                    version="1.0.0",
                    tarball_uri="s3://registry-artifacts/platform/v1/site-packages.tar.gz",
                ),
                RegistryArtifactsBackfillItem(
                    version_id=next_version_id,
                    version="2.0.0",
                    tarball_uri="s3://registry-artifacts/platform/v2/site-packages.tar.gz",
                ),
            ],
        )
    )

    assert result.requested_count == 2
    assert [item.version_id for item in result.results] == [
        failed_version_id,
        next_version_id,
    ]
    assert result.results[0].status == "failed"
    assert result.results[0].error == "RuntimeError: corrupt tarball"
    assert result.results[1].status == "created"
