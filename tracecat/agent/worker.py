"""AgentWorker - Temporal worker for agent workflow/control-plane execution."""

from __future__ import annotations

import asyncio
import dataclasses
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio import workflow
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from tracecat import __version__ as APP_VERSION

with workflow.unsafe.imports_passed_through():
    import sentry_sdk
    import uvloop
    from tracecat_ee.agent.activities import AgentActivities
    from tracecat_ee.agent.approvals.service import ApprovalManager
    from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow
    from tracecat_ee.agent.workflows.registry_tool import ExecuteRegistryToolWorkflow

    from tracecat import config
    from tracecat.agent.preset.activities import (
        resolve_agent_preset_config_activity,
        resolve_agent_preset_version_ref_activity,
        resolve_agents_config_activity,
        resolve_custom_model_provider_config_activity,
    )
    from tracecat.agent.session.activities import get_session_activities
    from tracecat.dsl.client import get_temporal_client
    from tracecat.dsl.interceptor import SentryInterceptor
    from tracecat.dsl.plugins import TracecatPydanticAIPlugin
    from tracecat.logger import logger
    from tracecat.temporal.worker_lifecycle import run_worker_entrypoint


def new_sandbox_runner() -> SandboxedWorkflowRunner:
    """Create a sandboxed workflow runner with datetime restrictions removed."""
    invalid_module_member_children = dict(
        SandboxRestrictions.invalid_module_members_default.children
    )
    del invalid_module_member_children["datetime"]

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


def get_activities() -> list[Callable[..., object]]:
    """Load all activities needed for agent workflow execution."""
    activities: list[Callable[..., object]] = []

    agent_activities = AgentActivities()
    activities.extend(agent_activities.get_activities())
    activities.extend(ApprovalManager.get_activities())
    activities.append(resolve_agent_preset_config_activity)
    activities.append(resolve_agent_preset_version_ref_activity)
    activities.append(resolve_agents_config_activity)
    activities.append(resolve_custom_model_provider_config_activity)
    activities.extend(get_session_activities())
    return activities


async def main(shutdown_event: asyncio.Event | None = None) -> None:
    """Run the AgentWorker."""
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    max_concurrent = int(
        os.environ.get("TRACECAT__AGENT_MAX_CONCURRENT_ACTIVITIES", 100)
    )
    threadpool_max_workers = int(
        os.environ.get("TEMPORAL__THREADPOOL_MAX_WORKERS", 100)
    )

    logger.info("Starting AgentWorker")

    client = await get_temporal_client(plugins=[TracecatPydanticAIPlugin()])

    interceptors = []
    if sentry_dsn := os.environ.get("SENTRY_DSN"):
        logger.info("Initializing Sentry interceptor")
        app_env = config.TRACECAT__APP_ENV
        temporal_namespace = config.TEMPORAL__CLUSTER_NAMESPACE
        sentry_environment = (
            config.SENTRY_ENVIRONMENT_OVERRIDE or f"{app_env}-{temporal_namespace}"
        )
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=sentry_environment,
            release=f"tracecat@{APP_VERSION}",
        )
        interceptors.append(SentryInterceptor())

    activities = get_activities()
    logger.debug(
        "Activities loaded",
        activities=[
            getattr(a, "__temporal_activity_definition").name for a in activities
        ],
    )

    with ThreadPoolExecutor(max_workers=threadpool_max_workers) as executor:
        workflows: list[type] = [DurableAgentWorkflow, ExecuteRegistryToolWorkflow]

        async with Worker(
            client,
            task_queue=config.TRACECAT__AGENT_QUEUE,
            activities=activities,
            workflows=workflows,
            workflow_runner=new_sandbox_runner(),
            interceptors=interceptors,
            max_concurrent_activities=max_concurrent,
            disable_eager_activity_execution=config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION,
            activity_executor=executor,
            graceful_shutdown_timeout=timedelta(seconds=30),
        ):
            logger.info("AgentWorker started, ctrl+c to exit")
            await shutdown_event.wait()
            logger.info("AgentWorker shutdown requested")
        logger.info("Temporal Worker context exited")


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    run_worker_entrypoint(main)
