"""Temporal workflow and activity for sandboxed registry sync.

This module defines the Temporal workflow that executes registry sync
operations on the ExecutorWorker with nsjail sandboxing for security.

Architecture:
    API Service                         ExecutorWorker
    (orchestrator)                      (tenant's task queue)
         │                                     │
         │  start_workflow(RegistrySyncWorkflow)
         │                         ┌───────────┴───────────┐
         │                         │  RegistrySyncWorkflow │
         │                         └───────────┬───────────┘
         │                    execute_activity(sync_registry)
         │                                     │
         │                                     ├─ Git clone (with SSH)
         │                                     ├─ Package install (nsjail)
         │                                     ├─ Action discovery (nsjail, NO network)
         │                                     ├─ Build execution artifact → upload to S3
         │  ◄── RegistrySyncResult ────────────┤
         │                                     │
         ├─ Create RegistryVersion             │
         └─ Populate RegistryIndex             │
"""

from __future__ import annotations

from datetime import timedelta
from typing import Never

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError

with workflow.unsafe.imports_passed_through():
    import tempfile
    from pathlib import Path

    from tracecat.logger import logger
    from tracecat.registry.artifact_keys import (
        get_squashfs_artifact_key,
        parse_s3_uri,
    )
    from tracecat.registry.sync.artifact import (
        build_squashfs_sidecar_from_tarball,
        download_tarball_venv,
    )
    from tracecat.registry.sync.runner import (
        ActionDiscoveryError,
        ArtifactBuildError,
        GitCloneError,
        PackageInstallError,
        RegistrySyncRunner,
        RegistrySyncRunnerError,
        RegistrySyncValidationError,
    )
    from tracecat.registry.sync.schemas import (
        RegistryArtifactsBackfillItem,
        RegistryArtifactsBackfillItemResult,
        RegistryArtifactsBackfillRequest,
        RegistryArtifactsBackfillResult,
        RegistrySyncFailureDiagnostic,
        RegistrySyncRequest,
        RegistrySyncResult,
    )
    from tracecat.runtime.errors import (
        RetryDisposition,
        RuntimeErrorClassification,
        RuntimeErrorKind,
    )
    from tracecat.sanitization import redact_sensitive_text
    from tracecat.storage import blob
    from tracecat.temporal.errors import (
        application_error_from_classification,
        build_error_transport_detail,
        extract_error_classification,
        raise_application_error_from_classification,
    )

# Leave the workflow time to report the final activity failure before the
# caller's 20-minute execution timeout expires.
_SYNC_ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT = timedelta(minutes=18)


@workflow.defn
class RegistrySyncWorkflow:
    """Minimal workflow wrapper for registry sync activity.

    This workflow runs on the tenant's dedicated task queue (enterprise)
    or the shared action queue. It wraps the single sync activity which
    performs all the heavy lifting in a sandboxed environment.
    """

    @workflow.run
    async def run(self, request: RegistrySyncRequest) -> RegistrySyncResult:
        """Execute the registry sync workflow.

        Args:
            request: Sync request containing repository details and SSH credentials.

        Returns:
            RegistrySyncResult with discovered actions and artifact URI.
        """
        workflow.logger.info(
            "Starting RegistrySyncWorkflow",
            repository_id=str(request.repository_id),
            origin=request.origin,
            origin_type=request.origin_type,
        )

        # Execute the sync activity with start_to_close timeout only.
        # We don't use heartbeat_timeout because the subprocess operations
        # (package install, action discovery) can take several minutes without
        # natural checkpoints for heartbeating. Bounded retries let the last
        # activity failure reach the caller instead of a workflow timeout.
        try:
            result = await workflow.execute_activity(
                sync_registry_activity,
                request,
                start_to_close_timeout=timedelta(minutes=15),
                schedule_to_close_timeout=_SYNC_ACTIVITY_SCHEDULE_TO_CLOSE_TIMEOUT,
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=5),
                ),
            )
        except ActivityError as exc:
            # Activity retries are exhausted. Fail the workflow non-retryably so
            # the caller's workflow retry policy cannot rerun the sync and hide
            # the final activity failure behind the execution timeout.
            raise _terminal_sync_failure(exc) from None

        workflow.logger.info(
            "RegistrySyncWorkflow completed",
            repository_id=str(request.repository_id),
            num_actions=len(result.actions),
            artifact_uri=result.artifact_uri,
        )

        return result


@workflow.defn
class RegistryArtifactsBackfillWorkflow:
    """Backfill artifacts for existing registry versions."""

    @workflow.run
    async def run(
        self,
        request: RegistryArtifactsBackfillRequest,
    ) -> RegistryArtifactsBackfillResult:
        """Build missing artifacts for selected registry versions."""
        workflow.logger.info(
            "Starting RegistryArtifactsBackfillWorkflow",
            requested_count=len(request.items),
        )

        results: list[RegistryArtifactsBackfillItemResult] = []
        for item in request.items:
            try:
                result = await workflow.execute_activity(
                    backfill_registry_artifacts_activity,
                    item,
                    start_to_close_timeout=timedelta(minutes=20),
                    retry_policy=RetryPolicy(
                        maximum_attempts=2,
                        initial_interval=timedelta(seconds=5),
                    ),
                )
            except ActivityError as exc:
                workflow.logger.warning(
                    "Registry artifact backfill item failed",
                    version_id=str(item.version_id),
                    version=item.version,
                    tarball_uri=item.tarball_uri,
                    error=str(exc.cause or exc),
                )
                result = RegistryArtifactsBackfillItemResult(
                    version_id=item.version_id,
                    status="failed",
                    error=f"{type(exc.cause).__name__}: {exc.cause}"
                    if exc.cause
                    else str(exc),
                )
            results.append(result)

        workflow.logger.info(
            "RegistryArtifactsBackfillWorkflow completed",
            requested_count=len(request.items),
            created_count=sum(1 for result in results if result.status == "created"),
            failed_count=sum(1 for result in results if result.status == "failed"),
        )
        return RegistryArtifactsBackfillResult(
            requested_count=len(request.items),
            results=results,
        )


def _raise_registry_sync_validation_error(
    message: str,
    *,
    cause: BaseException | None = None,
) -> Never:
    """Raise an attributed, non-retryable registry content failure."""
    raise_application_error_from_classification(
        RuntimeErrorClassification.user(
            kind=RuntimeErrorKind.REGISTRY_SYNC_VALIDATION_FAILED,
            message=message,
            retry_disposition=RetryDisposition.NON_RETRYABLE,
            cause=cause,
        )
    )


def _terminal_sync_failure(exc: ActivityError) -> BaseException:
    """Build the non-retryable workflow failure for an exhausted sync activity."""
    cause = exc.cause
    if isinstance(cause, TimeoutError):
        return application_error_from_classification(
            RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.EXECUTOR_ACTIVITY_TIMED_OUT,
                message="Registry sync timed out",
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=cause,
            )
        )
    if isinstance(cause, ApplicationError):
        if cause.non_retryable:
            return cause
        if extract_error_classification(cause) is not None:
            return ApplicationError(
                cause.message,
                *cause.details,
                type=cause.type,
                non_retryable=True,
            )
    return exc


def _raise_registry_sync_failure(exc: RegistrySyncRunnerError) -> Never:
    """Raise a retryable sync failure with a redacted diagnostic for the caller."""
    match exc:
        case GitCloneError():
            owner_classification = RuntimeErrorClassification.user
            message = "Registry repository clone failed"
        case PackageInstallError():
            owner_classification = RuntimeErrorClassification.user
            message = "Registry package installation failed"
        case ActionDiscoveryError():
            owner_classification = RuntimeErrorClassification.platform
            message = "Registry action discovery failed"
        case ArtifactBuildError():
            owner_classification = RuntimeErrorClassification.platform
            message = "Registry artifact build failed"
        case _:
            owner_classification = RuntimeErrorClassification.platform
            message = "Registry sync failed"
    classification = owner_classification(
        kind=RuntimeErrorKind.REGISTRY_SYNC_FAILED,
        message=message,
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=exc,
    )
    raise_application_error_from_classification(
        classification,
        build_error_transport_detail(
            classification,
            RegistrySyncFailureDiagnostic(detail=redact_sensitive_text(str(exc))),
        ),
    )


@activity.defn
async def sync_registry_activity(request: RegistrySyncRequest) -> RegistrySyncResult:
    """Execute registry sync with nsjail sandboxing.

    This activity runs on the ExecutorWorker and performs:
    1. Git clone (subprocess, needs SSH) - for git origins
    2. Package install (nsjail + network) - install dependencies
    3. Action discovery (nsjail, NO network) - import and discover actions
    4. Artifact build and upload - create portable registry environment

    Args:
        request: Sync request containing repository details.

    Returns:
        RegistrySyncResult with discovered actions and artifact URI.

    Raises:
        ApplicationError: If sync fails at any phase.
    """

    logger.info(
        "Starting sync_registry_activity",
        repository_id=str(request.repository_id),
        origin=request.origin,
        origin_type=request.origin_type,
    )

    # Create the runner and execute the sync
    runner = RegistrySyncRunner()
    try:
        result = await runner.run(request)
    except RegistrySyncValidationError as exc:
        # Validation failures are caused by repository content, not worker
        # capacity. Fail fast so users see the actionable error immediately.
        _raise_registry_sync_validation_error(str(exc), cause=exc)
    except ActionDiscoveryError as exc:
        if exc.non_retryable:
            # Some discovery failures are deterministic content errors, such as
            # template parsing failures. Keep transient subprocess failures retryable.
            _raise_registry_sync_validation_error(str(exc), cause=exc)
        _raise_registry_sync_failure(exc)
    except RegistrySyncRunnerError as exc:
        _raise_registry_sync_failure(exc)

    if result.validation_errors:
        error_count = sum(len(errs) for errs in result.validation_errors.values())
        first_action, first_errors = next(iter(result.validation_errors.items()))
        first_error = first_errors[0] if first_errors else None
        first_detail = (
            first_error.details[0]
            if first_error is not None and first_error.details
            else ""
        )
        _raise_registry_sync_validation_error(
            "Registry sync validation failed: "
            f"{error_count} validation error(s). "
            f"First error in '{first_action}': {first_detail}"
        )

    logger.info(
        "sync_registry_activity completed",
        repository_id=str(request.repository_id),
        num_actions=len(result.actions),
        artifact_uri=result.artifact_uri,
        commit_sha=result.commit_sha,
    )

    return result


@activity.defn
async def backfill_registry_artifacts_activity(
    item: RegistryArtifactsBackfillItem,
) -> RegistryArtifactsBackfillItemResult:
    """Build or verify missing SquashFS artifacts for a registry version."""
    try:
        bucket, artifact_key = parse_s3_uri(item.tarball_uri)
        squashfs_key = get_squashfs_artifact_key(artifact_key)
        artifact_uri = f"s3://{bucket}/{squashfs_key}"

        if await blob.file_exists(key=squashfs_key, bucket=bucket):
            logger.info(
                "Registry artifacts already exist",
                version_id=str(item.version_id),
                version=item.version,
                artifact_uri=artifact_uri,
            )
            return RegistryArtifactsBackfillItemResult(
                version_id=item.version_id,
                status="exists",
            )

        if artifact_key.endswith(".squashfs"):
            logger.info(
                "Registry artifact backfill skipped; SquashFS artifact is missing",
                version_id=str(item.version_id),
                version=item.version,
                artifact_uri=artifact_uri,
            )
            return RegistryArtifactsBackfillItemResult(
                version_id=item.version_id,
                status="skipped",
                error="SquashFS artifact is missing and no tarball source is available.",
            )

        with tempfile.TemporaryDirectory(
            prefix="tracecat_registry_artifacts_backfill_"
        ) as temp_dir:
            work_dir = Path(temp_dir)
            tarball_path = work_dir / "site-packages.tar.gz"
            squashfs_path = work_dir / "site-packages.squashfs"
            await download_tarball_venv(
                key=artifact_key,
                bucket=bucket,
                output_path=tarball_path,
            )
            created = await build_squashfs_sidecar_from_tarball(
                tarball_path=tarball_path,
                squashfs_path=squashfs_path,
                work_dir=work_dir / "extract",
            )
            if not created:
                logger.info(
                    "Registry artifact backfill skipped",
                    version_id=str(item.version_id),
                    version=item.version,
                )
                return RegistryArtifactsBackfillItemResult(
                    version_id=item.version_id,
                    status="skipped",
                    error="Artifact build is disabled or the builder is unavailable.",
                )

            await blob.upload_file_from_path(
                path=squashfs_path,
                key=squashfs_key,
                bucket=bucket,
                content_type="application/vnd.squashfs",
            )

        logger.info(
            "Registry artifacts backfilled",
            version_id=str(item.version_id),
            version=item.version,
            artifact_uri=artifact_uri,
        )
        return RegistryArtifactsBackfillItemResult(
            version_id=item.version_id,
            status="created",
        )
    except Exception:
        logger.exception(
            "Failed to backfill registry artifacts",
            version_id=str(item.version_id),
            version=item.version,
            tarball_uri=item.tarball_uri,
        )
        raise


class RegistrySyncActivities:
    """Container for registry sync activities.

    Follows the pattern established in ExecutorActivities for
    collecting activities to register with the Temporal worker.
    """

    @classmethod
    def get_activities(cls) -> list:
        """Return all registry sync activities for worker registration."""
        return [sync_registry_activity, backfill_registry_artifacts_activity]
