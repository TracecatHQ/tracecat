import asyncio
import dataclasses
import os
import signal
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from temporalio import workflow
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from tracecat import __version__ as APP_VERSION

with workflow.unsafe.imports_passed_through():
    import sentry_sdk
    from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

    from tracecat import config
    from tracecat.dsl.action import DSLActivities
    from tracecat.dsl.client import get_temporal_client
    from tracecat.dsl.interceptor import SentryInterceptor
    from tracecat.dsl.plugins import TracecatPydanticAIPlugin
    from tracecat.dsl.validation import (
        resolve_time_anchor_activity,
        resolve_workflow_concurrency_limits_enabled_activity,
    )
    from tracecat.dsl.workflow import DSLWorkflow
    from tracecat.ee.interactions.service import InteractionService
    from tracecat.logger import logger
    from tracecat.storage.collection import CollectionActivities
    from tracecat.tiers.activities import TierActivities
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
        resolve_registry_lock_activity,
    )
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.schedules.service import WorkflowSchedulesService
    from tracecat.workspaces.activities import get_workspace_organization_id_activity


# Due to known issues with Pydantic's use of issubclass and our inability to
# override the check in sandbox, Pydantic will think datetime is actually date
# in the sandbox. At the expense of protecting against datetime.now() use in
# workflows, we're going to remove datetime module restrictions. See sdk-python
# README's discussion of known sandbox issues for more details.
def new_sandbox_runner() -> SandboxedWorkflowRunner:
    # TODO(cretz): Use with_child_unrestricted when https://github.com/temporalio/sdk-python/issues/254
    # is fixed and released
    invalid_module_member_children = dict(
        SandboxRestrictions.invalid_module_members_default.children
    )
    del invalid_module_member_children["datetime"]

    # Pass through tracecat modules to avoid class identity mismatches
    # when Pydantic validates discriminated unions (e.g., StoredObject = InlineObject | ExternalObject)
    # Also pass through jsonpath_ng which is used for expression evaluation in workflows
    # Add beartype to passthrough modules to avoid circular import issues
    # with its custom import hooks conflicting with Temporal's sandbox
    passthrough_modules = SandboxRestrictions.passthrough_modules_default | {
        "tracecat",
        "tracecat_ee",
        "tracecat_registry",
        "jsonpath_ng",
        "dateparser",
        "beartype",
    }

    return SandboxedWorkflowRunner(
        restrictions=dataclasses.replace(
            SandboxRestrictions.default,
            invalid_module_members=dataclasses.replace(
                SandboxRestrictions.invalid_module_members_default,
                children=invalid_module_member_children,
            ),
            passthrough_modules=passthrough_modules,
        )
    )


interrupt_event = asyncio.Event()


def get_activities() -> list[Callable]:
    activities: list[Callable] = [
        *DSLActivities.load(),
        *CollectionActivities.get_activities(),
        get_workflow_definition_activity,
        resolve_registry_lock_activity,
        get_workspace_organization_id_activity,
        *WorkflowSchedulesService.get_activities(),
        resolve_time_anchor_activity,
        resolve_workflow_concurrency_limits_enabled_activity,
        *WorkflowsManagementService.get_activities(),
        *InteractionService.get_activities(),
        *TierActivities.get_activities(),
    ]
    return activities


async def main() -> None:
    # Enable workflow replay log filtering for this process
    from tracecat.logger import _logger

    _logger._is_worker_process = True

    client = await get_temporal_client(plugins=[TracecatPydanticAIPlugin()])

    interceptors = []
    if sentry_dsn := os.environ.get("SENTRY_DSN"):
        logger.info("Initializing Sentry interceptor")
        app_env = config.TRACECAT__APP_ENV
        temporal_namespace = config.TEMPORAL__CLUSTER_NAMESPACE
        sentry_environment: str = (
            config.SENTRY_ENVIRONMENT_OVERRIDE or f"{app_env}-{temporal_namespace}"
        )
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=sentry_environment,
            release=f"tracecat@{APP_VERSION}",
        )
        logger.info(
            "Sentry initialized",
            environment=sentry_environment,
            app_env=app_env,
            temporal_namespace=temporal_namespace,
        )
        interceptors.append(SentryInterceptor())

    # Run a worker for the activities and workflow
    activities = get_activities()
    logger.debug(
        "Activities loaded",
        activities=[
            getattr(a, "__temporal_activity_definition").name for a in activities
        ],
    )
    threadpool_max_workers = int(
        os.environ.get("TEMPORAL__THREADPOOL_MAX_WORKERS", 100)
    )

    with ThreadPoolExecutor(max_workers=threadpool_max_workers) as executor:
        workflows: list[type] = [DSLWorkflow, DurableAgentWorkflow]

        async with Worker(
            client,
            task_queue=os.environ.get("TEMPORAL__CLUSTER_QUEUE", "tracecat-task-queue"),
            activities=activities,
            workflows=workflows,
            workflow_runner=new_sandbox_runner(),
            interceptors=interceptors,
            disable_eager_activity_execution=config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION,
            activity_executor=executor,
        ):
            logger.info(
                "Worker started, ctrl+c to exit",
                disable_eager_activity_execution=config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION,
                threadpool_max_workers=threadpool_max_workers,
            )
            # Wait until interrupted
            await interrupt_event.wait()
            logger.info("Shutting down")


def _signal_handler(sig: int, _frame: object) -> None:
    """Handle shutdown signals gracefully.

    This mirrors the executor Temporal worker so the DSL worker can shut down
    cleanly on SIGINT/SIGTERM (e.g. `docker stop`, Kubernetes termination).
    """
    logger.info("Received shutdown signal", signal=sig)
    interrupt_event.set()


if __name__ == "__main__":
    # Install signal handlers before starting the event loop.
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
