"""AgentWorker - Temporal worker for agent workflow execution.

This worker listens on 'agent-action-queue' and executes DurableAgentWorkflow
instances. It is separate from the main DSLWorker to allow independent scaling
and isolation of agent workloads.

Services started at init:
- LiteLLM proxy subprocess (multi-tenant credential injection)
- Trusted MCP HTTP server (Unix socket for tool execution)

Architecture:
    Chat API / DSL agent action
        |
        v
    workflow.start_child_workflow(DurableAgentWorkflow, task_queue="agent-action-queue")
        |
        v
    AgentWorker picks up workflow
        |
        v
    DurableAgentWorkflow runs activities
        |
        v
    (NSJail path) Activity spawns jailed runtime, loopback forwards events
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import signal
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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

    from tracecat import config
    from tracecat.agent.executor.activity import (
        execute_approved_tools_activity,
        run_agent_activity,
    )
    from tracecat.agent.mcp.trusted_server import app
    from tracecat.agent.preset.activities import (
        resolve_agent_preset_config_activity,
    )
    from tracecat.agent.session.activities import get_session_activities
    from tracecat.dsl.client import get_temporal_client
    from tracecat.dsl.interceptor import SentryInterceptor
    from tracecat.dsl.plugins import TracecatPydanticAIPlugin
    from tracecat.logger import logger


def new_sandbox_runner() -> SandboxedWorkflowRunner:
    """Create a sandboxed workflow runner with datetime restrictions removed."""
    invalid_module_member_children = dict(
        SandboxRestrictions.invalid_module_members_default.children
    )
    del invalid_module_member_children["datetime"]

    # Add beartype to passthrough modules to avoid circular import issues
    # with its custom import hooks conflicting with Temporal's sandbox
    passthrough_modules = set(SandboxRestrictions.passthrough_modules_default)
    passthrough_modules.add("beartype")

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


# ============================================================================
# Service Lifecycle
# ============================================================================

_litellm_process: asyncio.subprocess.Process | None = None
_litellm_stderr_task: asyncio.Task[None] | None = None
_mcp_server_task: asyncio.Task[None] | None = None


async def _stream_litellm_stderr(process: asyncio.subprocess.Process) -> None:
    """Stream LiteLLM stderr to logger."""
    if process.stderr is None:
        return
    try:
        async for line in process.stderr:
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if decoded:
                logger.info("LiteLLM stderr", line=decoded)
    except Exception as e:
        logger.warning("LiteLLM stderr stream ended", error=str(e))


async def start_litellm_proxy() -> None:
    """Start the LiteLLM proxy subprocess."""
    global _litellm_process, _litellm_stderr_task

    # Use the config file from the tracecat.agent package
    source_config = Path(__file__).parent / "litellm_config.yaml"
    if not source_config.exists():
        logger.error("LiteLLM config not found", config_path=str(source_config))
        return

    # LiteLLM resolves custom_auth/callbacks paths relative to config file directory.
    # Create symlink at /app so that "tracecat.agent.gateway" resolves correctly
    # (dirname(/app/litellm_config.yaml) + tracecat/agent/gateway.py = /app/tracecat/agent/gateway.py)
    # Use atomic rename to prevent TOCTOU race condition
    runtime_config = Path("/app/litellm_config.yaml")
    temp_symlink = runtime_config.with_suffix(f".yaml.{os.getpid()}.tmp")
    try:
        temp_symlink.symlink_to(source_config)
        temp_symlink.replace(runtime_config)  # Atomic rename
    except FileExistsError:
        # Another process already created the symlink
        pass
    finally:
        # Clean up temp symlink if it still exists
        if temp_symlink.exists() or temp_symlink.is_symlink():
            temp_symlink.unlink()

    logger.info("Starting LiteLLM proxy")

    cmd = [
        "litellm",
        "--port",
        "4000",
        "--config",
        str(runtime_config),
    ]

    # Pass current environment with PYTHONPATH set to include /app
    # This allows LiteLLM to import tracecat modules for custom auth/callbacks
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    app_paths = "/app:/app/packages/tracecat-registry:/app/packages/tracecat-ee"
    env["PYTHONPATH"] = f"{app_paths}:{pythonpath}" if pythonpath else app_paths

    _litellm_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    # Start background task to stream stderr
    _litellm_stderr_task = asyncio.create_task(_stream_litellm_stderr(_litellm_process))

    logger.info("LiteLLM proxy started")


async def stop_litellm_proxy() -> None:
    """Stop the LiteLLM proxy subprocess."""
    global _litellm_process, _litellm_stderr_task

    # Cancel stderr streaming task
    if _litellm_stderr_task:
        _litellm_stderr_task.cancel()
        try:
            await _litellm_stderr_task
        except asyncio.CancelledError:
            pass
        _litellm_stderr_task = None

    if _litellm_process and _litellm_process.returncode is None:
        logger.info("Stopping LiteLLM proxy")
        _litellm_process.terminate()
        try:
            await asyncio.wait_for(_litellm_process.wait(), timeout=5.0)
        except TimeoutError:
            _litellm_process.kill()
            await _litellm_process.wait()
        _litellm_process = None


async def start_mcp_server() -> None:
    """Start the trusted MCP HTTP server on Unix socket."""
    global _mcp_server_task

    import uvicorn

    from tracecat.agent.common.config import TRUSTED_MCP_SOCKET_PATH

    socket_path = TRUSTED_MCP_SOCKET_PATH
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    if socket_path.exists():
        socket_path.unlink()

    logger.info("Starting MCP server", socket_path=str(socket_path))

    uvicorn_config = uvicorn.Config(
        app,
        uds=str(socket_path),
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)
    _mcp_server_task = asyncio.create_task(server.serve())

    # Wait for socket to be created
    for _ in range(50):
        if socket_path.exists():
            break
        await asyncio.sleep(0.1)

    if socket_path.exists():
        os.chmod(str(socket_path), 0o600)

    logger.info("MCP server started", socket_path=str(socket_path))


async def stop_mcp_server() -> None:
    """Stop the MCP server."""
    global _mcp_server_task

    if _mcp_server_task:
        logger.info("Stopping MCP server")
        _mcp_server_task.cancel()
        try:
            await _mcp_server_task
        except asyncio.CancelledError:
            pass
        _mcp_server_task = None


# ============================================================================
# Activities
# ============================================================================


def get_activities() -> list[Callable[..., object]]:
    """Load all activities needed for agent workflow execution."""
    activities: list[Callable[..., object]] = []

    # Agent activities (EE)
    agent_activities = AgentActivities()
    activities.extend(agent_activities.get_activities())

    # Approval activities (EE)
    activities.extend(ApprovalManager.get_activities())
    # Preset resolution
    activities.append(resolve_agent_preset_config_activity)

    # Session management activities
    activities.extend(get_session_activities())

    # Agent executor activity
    activities.append(run_agent_activity)
    activities.append(execute_approved_tools_activity)

    return activities


# ============================================================================
# Main
# ============================================================================


async def main() -> None:
    """Run the AgentWorker."""
    max_concurrent = int(
        os.environ.get("TRACECAT__AGENT_MAX_CONCURRENT_ACTIVITIES", 100)
    )
    threadpool_max_workers = int(
        os.environ.get("TEMPORAL__THREADPOOL_MAX_WORKERS", 100)
    )

    logger.info(
        "Starting AgentWorker",
    )

    # Initialize services before accepting tasks
    await start_litellm_proxy()
    await start_mcp_server()

    try:
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
            workflows: list[type] = [DurableAgentWorkflow]

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
            ):
                logger.info(
                    "AgentWorker started, ctrl+c to exit",
                )
                await interrupt_event.wait()
                logger.info("Shutting down AgentWorker")

    finally:
        logger.info("Shutting down services")
        await stop_mcp_server()
        await stop_litellm_proxy()


def _signal_handler(sig: int, _frame: object) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("Received shutdown signal", signal=sig)
    interrupt_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
