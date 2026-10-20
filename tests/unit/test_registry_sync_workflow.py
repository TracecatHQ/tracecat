"""Tests for registry sync Temporal workflow activity behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    TimeoutError,
    TimeoutType,
)

from tracecat.exceptions import (
    RegistryError,
    RegistrySyncContentError,
    RegistrySyncSettingsError,
    TracecatCredentialsNotFoundError,
)
from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo
from tracecat.registry.sync.artifact import (
    RegistryArtifactBuildError,
)
from tracecat.registry.sync.runner import (
    ActionDiscoveryError,
    ArtifactBuildError,
    GitCloneError,
    PackageInstallError,
    RegistrySyncRunnerError,
    RegistrySyncValidationError,
)
from tracecat.registry.sync.schemas import (
    RegistryArtifactsBackfillItem,
    RegistryArtifactsBackfillItemResult,
    RegistryArtifactsBackfillRequest,
    RegistrySyncFailureDiagnostic,
    RegistrySyncRequest,
    SyncErrorCode,
)
from tracecat.registry.sync.workflow import (
    RegistryArtifactsBackfillWorkflow,
    RegistrySyncWorkflow,
    _raise_registry_sync_failure,
    backfill_registry_artifacts_activity,
    sync_registry_activity,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxTimeoutError,
    SandboxWorkloadError,
)
from tracecat.sandbox.types import SandboxErrorCode
from tracecat.temporal.errors import (
    extract_error_classification,
    extract_error_classifications,
    extract_error_diagnostics,
)


def _assert_registry_validation_classification(
    error: ApplicationError, owner: RuntimeErrorOwner = RuntimeErrorOwner.USER
) -> None:
    classification = extract_error_classifications(error)

    assert len(classification) == 1
    assert classification[0].owner is owner
    assert classification[0].kind is RuntimeErrorKind.REGISTRY_SYNC_VALIDATION_FAILED
    assert classification[0].retry_disposition is RetryDisposition.NON_RETRYABLE
    assert error.type == RuntimeErrorKind.REGISTRY_SYNC_VALIDATION_FAILED.value
    assert error.non_retryable is True


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
        origin="git+ssh://git@git.example.test/acme/registry.git",
        origin_type="git",
    )

    with pytest.raises(
        ApplicationError,
        match="Registry sync validation failed",
    ) as exc_info:
        await sync_registry_activity(request)

    _assert_registry_validation_classification(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("origin_type", "owner"),
    [
        pytest.param("git", RuntimeErrorOwner.USER, id="git"),
        pytest.param("builtin", RuntimeErrorOwner.PLATFORM, id="builtin"),
    ],
)
async def test_sync_registry_activity_raises_non_retryable_error_for_content_discovery_failure(
    monkeypatch: pytest.MonkeyPatch,
    origin_type: Literal["builtin", "git"],
    owner: RuntimeErrorOwner,
) -> None:
    """Discovery/template-load failures should surface immediately, not retry to timeout."""

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            detail = (
                "Failed to load template action from "
                "/custom_actions/example_template.yml: "
                "Invalid type annotation for expected field 'items': 'list'. "
                "Lists must include an item type, e.g. 'list[str]' or 'list[Any]'."
            )
            raise ActionDiscoveryError(
                f"Failed to discover actions: {detail}"
            ) from RegistrySyncContentError(
                detail, code=SyncErrorCode.TEMPLATE_LOAD_FAILED
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="git+ssh://git@git.example.test/acme/registry.git",
        origin_type=origin_type,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await sync_registry_activity(request)

    exc = exc_info.value
    _assert_registry_validation_classification(exc, owner)
    assert "example_template.yml" in str(exc)
    assert "list[str]" in str(exc)


def _registry_sync_failure_detail(error: ApplicationError) -> str:
    classification = extract_error_classification(error)
    assert classification is not None
    (diagnostic,) = extract_error_diagnostics(error, classification)
    return RegistrySyncFailureDiagnostic.model_validate(diagnostic).detail


_GIT_ORIGIN = "git+ssh://git@git.example.test/acme/registry.git"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("runner_error", "cause", "origin_type", "owner", "retry", "message", "detail"),
    [
        pytest.param(
            ActionDiscoveryError(
                "Failed to discover actions: Sync subprocess timed out after 300s"
            ),
            RegistryError("Sync subprocess timed out after 300s"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry action discovery failed",
            "Failed to discover actions: Sync subprocess timed out after 300s",
            id="discovery_crash",
        ),
        pytest.param(
            GitCloneError(
                "Git clone failed: https://user:synthetic-password@git.example.com/r.git"
            ),
            RegistrySyncSettingsError("Registry sync Git host is invalid"),
            "git",
            RuntimeErrorOwner.USER,
            RetryDisposition.NON_RETRYABLE,
            "Registry repository clone failed",
            "Git clone failed: https://git.example.com/r.git",
            id="git_settings_redacted",
        ),
        pytest.param(
            GitCloneError("Failed to clone repository: remote: 503"),
            RegistryError("Failed to clone repository: remote: 503"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry repository clone failed",
            "Failed to clone repository: remote: 503",
            id="git_command_failure",
        ),
        pytest.param(
            GitCloneError("Sandboxed Git clone failed: clone timed out"),
            SandboxTimeoutError("clone timed out"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry repository clone failed",
            "Sandboxed Git clone failed: clone timed out",
            id="git_timeout",
        ),
        pytest.param(
            GitCloneError("Sandboxed Git clone returned invalid output"),
            RegistryError("Sandboxed Git clone returned invalid output"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry repository clone failed",
            "Sandboxed Git clone returned invalid output",
            id="git_clone_invalid_output",
        ),
        pytest.param(
            GitCloneError("Failed to retrieve SSH key: not found"),
            TracecatCredentialsNotFoundError("SSH key not found"),
            "git",
            RuntimeErrorOwner.USER,
            RetryDisposition.NON_RETRYABLE,
            "Registry repository clone failed",
            "Failed to retrieve SSH key: not found",
            id="ssh_key_missing",
        ),
        pytest.param(
            GitCloneError("Failed to retrieve SSH key: database unavailable"),
            ConnectionError("database unavailable"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry repository clone failed",
            "Failed to retrieve SSH key: database unavailable",
            id="ssh_key_outage",
        ),
        pytest.param(
            GitCloneError("Sandboxed Git clone failed before the workload started"),
            SandboxInfrastructureError("nsjail launch failed"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry repository clone failed",
            "Sandboxed Git clone failed before the workload started",
            id="git_clone_infrastructure",
        ),
        pytest.param(
            PackageInstallError("Package installation failed: 503 from index"),
            RegistryArtifactBuildError("Package installation failed: 503 from index"),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry package installation failed",
            "Package installation failed: 503 from index",
            id="package_install_failure",
        ),
        pytest.param(
            PackageInstallError("Sandboxed registry package installation stopped"),
            SandboxWorkloadError(
                "Sandboxed registry package installation stopped",
                error_code=SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            ),
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry package installation failed",
            "Sandboxed registry package installation stopped",
            id="package_install_phase_limit",
        ),
        pytest.param(
            ArtifactBuildError("Sandboxed registry packaging failed: mksquashfs"),
            None,
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry artifact build failed",
            "Sandboxed registry packaging failed: mksquashfs",
            id="artifact_build",
        ),
        pytest.param(
            RegistrySyncRunnerError("git_url is required for git origin type"),
            None,
            "git",
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Registry sync failed",
            "git_url is required for git origin type",
            id="generic",
        ),
    ],
)
async def test_sync_registry_activity_classifies_sync_failures(
    monkeypatch: pytest.MonkeyPatch,
    runner_error: RegistrySyncRunnerError,
    cause: BaseException | None,
    origin_type: Literal["builtin", "git"],
    owner: RuntimeErrorOwner,
    retry: RetryDisposition,
    message: str,
    detail: str,
) -> None:
    """Typed causes decide ownership; user failures that repeat are not retried."""

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            raise runner_error from cause

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry" if origin_type == "builtin" else _GIT_ORIGIN,
        origin_type=origin_type,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await sync_registry_activity(request)

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is owner
    assert classification.kind is RuntimeErrorKind.REGISTRY_SYNC_FAILED
    assert classification.retry_disposition is retry
    assert error.message == message
    assert error.non_retryable is (retry is RetryDisposition.NON_RETRYABLE)
    assert error.__cause__ is None
    assert _registry_sync_failure_detail(error) == detail


@pytest.mark.anyio
async def test_sync_registry_activity_redacts_validation_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            raise ActionDiscoveryError(
                "Failed to discover actions: Authorization: Bearer synthetic-token"
            ) from RegistrySyncContentError(
                "Authorization: Bearer synthetic-token",
                code=SyncErrorCode.IMPORT_FAILED,
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    with pytest.raises(ApplicationError) as exc_info:
        await sync_registry_activity(
            RegistrySyncRequest(
                repository_id=uuid4(), origin=_GIT_ORIGIN, origin_type="git"
            )
        )

    _assert_registry_validation_classification(exc_info.value)
    assert "synthetic-token" not in str(exc_info.value)
    (classification,) = extract_error_classifications(exc_info.value)
    assert "synthetic-token" not in classification.message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("artifact_name", "squashfs_exists", "status", "error"),
    [
        pytest.param(
            "site-packages.tar.gz", True, "exists", None, id="tarball_with_sidecar"
        ),
        pytest.param(
            "site-packages.squashfs", True, "exists", None, id="existing_squashfs"
        ),
        pytest.param(
            "site-packages.squashfs",
            False,
            "skipped",
            "SquashFS artifact is missing and no tarball source is available.",
            id="missing_squashfs",
        ),
    ],
)
async def test_backfill_registry_artifacts_activity_checks_existing_squashfs(
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
    squashfs_exists: bool,
    status: str,
    error: str | None,
) -> None:
    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        assert bucket == "registry-artifacts"
        assert key == "platform/tarball-venvs/test/1.0.0/site-packages.squashfs"
        return squashfs_exists

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

    version_id = uuid4()
    result = await backfill_registry_artifacts_activity(
        RegistryArtifactsBackfillItem(
            version_id=version_id,
            version="1.0.0",
            tarball_uri=(
                f"s3://registry-artifacts/platform/tarball-venvs/test/1.0.0/{artifact_name}"
            ),
        )
    )

    assert result.status == status
    assert result.version_id == version_id
    if error is not None:
        assert result.error == error


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


async def _run_workflow_with_activity_failure(
    monkeypatch: pytest.MonkeyPatch,
    cause: BaseException,
) -> BaseException:
    async def fake_execute_activity(*_args: Any, **_kwargs: Any) -> Any:
        try:
            raise cause
        except BaseException as exc:
            raise ActivityError(
                "Activity task failed",
                scheduled_event_id=1,
                started_event_id=2,
                identity="test",
                activity_type="sync_registry_activity",
                activity_id="activity-id",
                retry_state=None,
            ) from exc

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.workflow.execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.workflow.logger",
        SimpleNamespace(info=lambda *_args, **_kwargs: None),
    )
    request: Any = SimpleNamespace(
        repository_id=uuid4(), origin="custom_actions", origin_type="git"
    )
    with pytest.raises(BaseException) as exc_info:
        await RegistrySyncWorkflow().run(request)
    return exc_info.value


@pytest.mark.anyio
async def test_registry_sync_workflow_reraises_exhausted_activity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ApplicationError) as activity_failure:
        _raise_registry_sync_failure(
            ActionDiscoveryError("Failed to discover actions: worker lost"),
            RegistrySyncRequest(
                repository_id=uuid4(), origin=_GIT_ORIGIN, origin_type="git"
            ),
        )
    cause = activity_failure.value

    error = await _run_workflow_with_activity_failure(monkeypatch, cause)

    assert isinstance(error, ActivityError)
    assert error.cause is cause
    assert extract_error_classification(cause) is not None
    assert _registry_sync_failure_detail(cause) == (
        "Failed to discover actions: worker lost"
    )


@pytest.mark.anyio
async def test_registry_sync_workflow_classifies_activity_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cause = TimeoutError(
        "activity timeout",
        type=TimeoutType.SCHEDULE_TO_CLOSE,
        last_heartbeat_details=[],
    )

    error = await _run_workflow_with_activity_failure(monkeypatch, cause)

    assert isinstance(error, ApplicationError)
    assert error.non_retryable is True
    assert error.message == "Registry sync timed out"
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.kind is RuntimeErrorKind.EXECUTOR_ACTIVITY_TIMED_OUT
