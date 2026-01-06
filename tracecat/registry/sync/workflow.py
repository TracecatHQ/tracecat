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
         │                                     ├─ Build tarball → upload to S3
         │  ◄── RegistrySyncResult ────────────┤
         │                                     │
         ├─ Create RegistryVersion             │
         └─ Populate RegistryIndex             │
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from tracecat.logger import logger
    from tracecat.registry.sync.runner import RegistrySyncRunner
    from tracecat.registry.sync.schemas import RegistrySyncRequest, RegistrySyncResult


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
            RegistrySyncResult with discovered actions and tarball URI.
        """
        workflow.logger.info(
            "Starting RegistrySyncWorkflow",
            repository_id=str(request.repository_id),
            origin=request.origin,
            origin_type=request.origin_type,
        )

        # Execute the sync activity with appropriate timeouts
        # - start_to_close_timeout: Total time allowed for the activity
        # - heartbeat_timeout: Activity must heartbeat within this interval
        result = await workflow.execute_activity(
            sync_registry_activity,
            request,
            start_to_close_timeout=timedelta(minutes=15),
            heartbeat_timeout=timedelta(minutes=2),
        )

        workflow.logger.info(
            "RegistrySyncWorkflow completed",
            repository_id=str(request.repository_id),
            num_actions=len(result.actions),
            tarball_uri=result.tarball_uri,
        )

        return result


@activity.defn
async def sync_registry_activity(request: RegistrySyncRequest) -> RegistrySyncResult:
    """Execute registry sync with nsjail sandboxing.

    This activity runs on the ExecutorWorker and performs:
    1. Git clone (subprocess, needs SSH) - for git origins
    2. Package install (nsjail + network) - install dependencies
    3. Action discovery (nsjail, NO network) - import and discover actions
    4. Tarball build and upload - create portable venv

    Args:
        request: Sync request containing repository details.

    Returns:
        RegistrySyncResult with discovered actions and tarball URI.

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
    result = await runner.run(request)

    logger.info(
        "sync_registry_activity completed",
        repository_id=str(request.repository_id),
        num_actions=len(result.actions),
        tarball_uri=result.tarball_uri,
        commit_sha=result.commit_sha,
    )

    return result


class RegistrySyncActivities:
    """Container for registry sync activities.

    Follows the pattern established in ExecutorActivities for
    collecting activities to register with the Temporal worker.
    """

    @classmethod
    def get_activities(cls) -> list:
        """Return all registry sync activities for worker registration."""
        return [sync_registry_activity]
