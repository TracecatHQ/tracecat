"""Action runner for subprocess-based execution (untrusted mode).

This module provides the ActionRunner class that executes registry actions
in isolated subprocesses with registry artifact caching.

All execution is untrusted - DB credentials are never passed to subprocesses.
Secrets and variables are pre-resolved on the host.

Key features:
- Registry artifacts: Materializes pre-built registry environments from S3
- Caching: Reuses materialized environments by cache key for fast subsequent runs
- Subprocess execution: Runs actions via minimal_runner.py
- nsjail sandboxing: Optional OS-level isolation with resource limits
- Timeout handling: Kills subprocess on timeouts
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
from pydantic_core import to_json

from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.executor.action_gateway.config import (
    ACTION_GATEWAY_SANDBOX_SOCKET,
    action_gateway_socket_path,
)
from tracecat.executor.registry_artifacts import RegistryArtifactCache
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import (
    SecretEnvProjection,
    project_secret_env,
)
from tracecat.logger import logger
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.types import ResourceLimits
from tracecat.secrets.common import apply_masks, apply_masks_object

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput

type ExecutionResult = Any | ExecutorActionErrorInfo


def _get_tracecat_app_dir() -> Path:
    """Get the tracecat package directory for sandbox mounting.

    Returns the directory containing the tracecat package, which will be
    mounted read-only in the sandbox at /app.
    """
    # Check if explicitly configured
    if config.TRACECAT__EXECUTOR_TRACECAT_APP_DIR:
        return Path(config.TRACECAT__EXECUTOR_TRACECAT_APP_DIR)

    # Auto-detect from installed package location
    import tracecat

    tracecat_path = Path(tracecat.__file__).parent
    # Return parent (site-packages) so sandbox can import tracecat
    return tracecat_path.parent


def _get_site_packages_dir() -> Path | None:
    """Get the site-packages directory containing Python dependencies.

    Returns the site-packages directory where tracecat's dependencies
    (orjson, pydantic, sqlalchemy, etc.) are installed.
    """
    # Check if explicitly configured
    if config.TRACECAT__EXECUTOR_SITE_PACKAGES_DIR:
        return Path(config.TRACECAT__EXECUTOR_SITE_PACKAGES_DIR)

    # Auto-detect using sysconfig
    import sysconfig

    site_packages = sysconfig.get_path("purelib")
    if site_packages:
        return Path(site_packages)
    return None


def _is_sandbox_available() -> bool:
    """Check if nsjail sandbox is available."""
    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)

    if not nsjail_path.exists():
        logger.debug("nsjail binary not found", path=str(nsjail_path))
        return False

    if not rootfs_path.exists():
        logger.debug("Sandbox rootfs not found", path=str(rootfs_path))
        return False

    return True


def _direct_subprocess_command(minimal_runner_path: Path) -> list[str]:
    """Build the direct action subprocess command with new privileges disabled."""
    runner_command = [sys.executable, str(minimal_runner_path)]
    if sys.platform != "linux":
        return runner_command

    setpriv = shutil.which("setpriv")
    if setpriv is None:
        raise RuntimeError("setpriv is required for direct action subprocess isolation")

    return [
        setpriv,
        "--no-new-privs",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        *runner_command,
    ]


class ActionRunner:
    """Runs registry actions in subprocesses with registry artifact caching.

    This runner:
    1. Materializes registry artifact URIs to cached Python paths
    2. Builds subprocess or sandbox execution environments
    3. Executes the action in a subprocess with PYTHONPATH set
    4. Returns the result or error
    """

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or Path(config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR)
        self.registry_artifacts = RegistryArtifactCache(self.cache_dir)
        logger.info("ActionRunner initialized", cache_dir=str(self.cache_dir))

    async def ensure_registry_environment(self, artifact_uri: str | None) -> list[Path]:
        """Ensure the registry environment is set up and return PYTHONPATH entries.

        This is the public API for pool workers to get the paths to add to PYTHONPATH.

        Args:
            artifact_uri: S3 URI to the registry execution artifact.

        Returns:
            Paths to add to PYTHONPATH (empty if no artifact is available).
        """
        return await self.registry_artifacts.ensure_environment(artifact_uri)

    async def resolve_registry_paths(
        self, artifact_uris: list[str] | None = None
    ) -> list[Path]:
        """Materialize registry artifacts and return importable Python paths."""
        registry_paths: list[Path] = []
        if artifact_uris:
            for artifact_uri in artifact_uris:
                registry_paths.extend(
                    await self.ensure_registry_environment(artifact_uri)
                )
            logger.info(
                "Using registry artifact environments",
                count=len(registry_paths),
            )
            return registry_paths

        base_dir = self.cache_dir / "base"
        base_dir.mkdir(parents=True, exist_ok=True)
        logger.info("No registry artifact URIs provided, using base PYTHONPATH")
        return [base_dir]

    async def execute_action(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        artifact_uris: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
        force_sandbox: bool = False,
    ) -> ExecutionResult:
        """Execute an action in a subprocess (untrusted mode).

        All execution is in untrusted mode - DB credentials are never passed
        to subprocesses. Secrets and variables are pre-resolved on the host.

        Args:
            input: The RunActionInput containing task and context
            role: The Role for authorization
            artifact_uris: List of registry artifact S3 URIs (deterministic order)
            env_vars: Additional environment variables for the subprocess
            timeout: Execution timeout in seconds
            force_sandbox: If True, always use nsjail sandbox regardless of config
            resolved_context: Pre-resolved context from service layer (optional,
                will be resolved here if not provided)

        Returns:
            The action result, or ExecutorActionErrorInfo on error
        """
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Materialize each registry artifact, collect paths in deterministic order.
        registry_paths = await self.resolve_registry_paths(artifact_uris)

        # Check if sandbox execution is enabled and available
        # force_sandbox=True overrides config (used by ephemeral backend)
        use_sandbox = force_sandbox or (
            config.TRACECAT__EXECUTOR_SANDBOX_ENABLED and _is_sandbox_available()
        )
        logger.debug(
            "Using sandbox execution",
            use_sandbox=use_sandbox,
            force_sandbox=force_sandbox,
        )

        secret_projection = resolved_context.secret_projection
        if secret_projection is None:
            secret_projection = await project_secret_env(
                secrets=resolved_context.secrets,
                role=role,
                run_context=input.run_context,
            )

        if use_sandbox:
            return await self._execute_sandboxed(
                input=input,
                role=role,
                registry_paths=registry_paths,
                secret_projection=secret_projection,
                env_vars=env_vars,
                timeout=timeout,
                resolved_context=resolved_context,
            )
        else:
            return await self._execute_direct(
                input=input,
                role=role,
                registry_paths=registry_paths,
                secret_projection=secret_projection,
                env_vars=env_vars,
                timeout=timeout,
                resolved_context=resolved_context,
            )

    async def _execute_sandboxed(
        self,
        input: RunActionInput,
        role: Role,
        registry_paths: list[Path],
        secret_projection: SecretEnvProjection,
        resolved_context: ResolvedContext,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute an action in an nsjail sandbox (untrusted mode).

        All sandbox execution is untrusted - DB credentials are never passed.
        Secrets and variables must be pre-resolved and passed via resolved_context.

        Args:
            input: The RunActionInput containing task and context
            role: The Role for authorization
            registry_paths: List of directories containing extracted registry tarballs
            resolved_context: Pre-resolved secrets, variables, and action impl
            env_vars: Additional environment variables for the subprocess
            timeout: Execution timeout in seconds
        """
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Create temporary directory for file-based IPC
        job_dir = Path(tempfile.mkdtemp(prefix="tracecat_action_"))

        try:
            # Build payload with resolved_context
            payload: dict[str, Any] = {
                "input": input,
                "role": role,
                "resolved_context": resolved_context,
                "secret_env": secret_projection.env,
            }

            # Write input JSON to job directory
            input_json = to_json(payload)
            input_path = job_dir / "input.json"
            input_path.write_bytes(input_json)

            # Copy minimal_runner.py to job directory
            from tracecat.executor import minimal_runner as minimal_runner_module

            minimal_runner_src = Path(minimal_runner_module.__file__)
            minimal_runner_dst = job_dir / "minimal_runner.py"
            shutil.copy2(minimal_runner_src, minimal_runner_dst)

            # Build environment variables for sandbox (untrusted mode only)
            # NOTE: DB credentials are intentionally NOT passed
            sandbox_env: dict[str, str] = {}
            if env_vars:
                sandbox_env.update(env_vars)

            # SDK context for any registry SDK operations
            sandbox_env["TRACECAT__API_URL"] = config.TRACECAT__API_URL
            sandbox_env["TRACECAT__WORKSPACE_ID"] = (
                str(role.workspace_id) if role.workspace_id else ""
            )
            sandbox_env["TRACECAT__WORKFLOW_ID"] = str(input.run_context.wf_id)
            sandbox_env["TRACECAT__RUN_ID"] = str(input.run_context.wf_run_id)
            sandbox_env["TRACECAT__WF_EXEC_ID"] = str(input.run_context.wf_exec_id)
            sandbox_env["TRACECAT__ENVIRONMENT"] = input.run_context.environment
            action_gateway_socket = action_gateway_socket_path()
            if action_gateway_socket is not None:
                sandbox_env["TRACECAT__ACTION_GATEWAY_SOCKET"] = str(
                    ACTION_GATEWAY_SANDBOX_SOCKET
                )
            else:
                sandbox_env.pop("TRACECAT__ACTION_GATEWAY_SOCKET", None)

            # Mint an executor token for SDK calls
            if role.workspace_id is None:
                raise ValueError("workspace_id is required for sandbox execution")
            executor_token = mint_executor_token(
                workspace_id=role.workspace_id,
                user_id=role.user_id,
                service_id=role.service_id,
                wf_id=str(input.run_context.wf_id),
                wf_exec_id=str(input.run_context.wf_run_id),
            )
            sandbox_env["TRACECAT__EXECUTOR_TOKEN"] = executor_token

            logger.debug(
                "Using untrusted mode - no DB credentials passed to sandbox",
                action=input.task.action,
            )

            # Configure sandbox
            sandbox_config = ActionSandboxConfig(
                registry_paths=registry_paths,
                tracecat_app_dir=_get_tracecat_app_dir(),
                site_packages_dir=_get_site_packages_dir(),
                env_vars=sandbox_env,
                action_gateway_socket=action_gateway_socket,
                action_gateway_socket_mount_path=ACTION_GATEWAY_SANDBOX_SOCKET,
                resources=ResourceLimits(
                    memory_mb=config.TRACECAT__SANDBOX_DEFAULT_MEMORY_MB,
                    timeout_seconds=int(timeout),
                ),
                timeout_seconds=timeout,
            )

            logger.debug(
                "Executing action in nsjail sandbox",
                action=input.task.action,
                job_dir=str(job_dir),
                registry_paths_count=len(registry_paths),
            )

            # Execute in sandbox
            start_time = time.monotonic()
            executor = NsjailExecutor()
            result = await executor.execute_action(job_dir, sandbox_config)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "Sandbox execution completed",
                action=input.task.action,
                elapsed_ms=f"{elapsed_ms:.1f}",
                success=result.success,
                exit_code=result.exit_code,
            )

            # Process result
            if result.success:
                return result.output

            # Handle error from sandbox
            if result.error:
                masked_error = apply_masks_object(
                    result.error, masks=secret_projection.mask_values
                )
                # Try to parse as ExecutorActionErrorInfo
                if isinstance(masked_error, dict):
                    return ExecutorActionErrorInfo.model_validate(masked_error)
                return ExecutorActionErrorInfo(
                    type="SandboxError",
                    message=str(masked_error),
                    action_name=input.task.action,
                    filename="<sandbox>",
                    function="execute_action",
                )

            return ExecutorActionErrorInfo(
                type="SandboxError",
                message=f"Sandbox execution failed with exit code {result.exit_code}",
                action_name=input.task.action,
                filename="<sandbox>",
                function="execute_action",
            )

        finally:
            # Cleanup job directory
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _execute_direct(
        self,
        input: RunActionInput,
        role: Role,
        registry_paths: list[Path],
        secret_projection: SecretEnvProjection,
        env_vars: dict[str, str] | None = None,
        timeout: float | None = None,
        resolved_context: ResolvedContext | None = None,
    ) -> ExecutionResult:
        """Execute an action in a direct subprocess (no sandbox)."""
        timeout = timeout or config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

        # Prepare input JSON for subprocess
        payload: dict[str, Any] = {"input": input, "role": role}
        if resolved_context is not None:
            payload["resolved_context"] = resolved_context
            payload["secret_env"] = secret_projection.env
        input_json = to_json(payload)

        # Build environment with registry paths in PYTHONPATH
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        # Ensure SDK context is available for registry actions executed by minimal_runner.
        if resolved_context is not None:
            env["TRACECAT__API_URL"] = config.TRACECAT__API_URL
            env["TRACECAT__WORKSPACE_ID"] = resolved_context.workspace_id
            env["TRACECAT__WORKFLOW_ID"] = resolved_context.workflow_id
            env["TRACECAT__RUN_ID"] = resolved_context.run_id
            env["TRACECAT__WF_EXEC_ID"] = str(input.run_context.wf_exec_id)
            env["TRACECAT__ENVIRONMENT"] = input.run_context.environment
            env["TRACECAT__EXECUTOR_TOKEN"] = resolved_context.executor_token
            if socket_path := action_gateway_socket_path():
                env["TRACECAT__ACTION_GATEWAY_SOCKET"] = str(socket_path)
            else:
                env.pop("TRACECAT__ACTION_GATEWAY_SOCKET", None)

        # Build PYTHONPATH with multiple registry paths (deterministic order)
        pythonpath_parts = [str(p) for p in registry_paths if p.exists()]
        existing_pythonpath = env.get("PYTHONPATH", "")
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = ":".join(pythonpath_parts) if pythonpath_parts else ""

        # Get path to minimal_runner.py for subprocess execution
        from tracecat.executor import minimal_runner as minimal_runner_module

        minimal_runner_path = Path(minimal_runner_module.__file__)
        try:
            command = _direct_subprocess_command(minimal_runner_path)
        except RuntimeError as e:
            logger.error("Failed to prepare direct action subprocess", error=str(e))
            return ExecutorActionErrorInfo(
                type="SubprocessError",
                message=str(e),
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        logger.debug(
            "Executing action in subprocess",
            action=input.task.action,
            timeout=timeout,
        )

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_json),
                timeout=timeout,
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "Subprocess execution completed",
                action=input.task.action,
                elapsed_ms=f"{elapsed_ms:.1f}",
                returncode=proc.returncode,
            )
        except TimeoutError:
            logger.error(
                "Action execution timed out, killing subprocess",
                action=input.task.action,
                timeout=timeout,
            )
            proc.kill()
            await proc.wait()
            return ExecutorActionErrorInfo(
                type="TimeoutError",
                message=f"Action execution timed out after {timeout}s",
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        # Check for subprocess crash
        if proc.returncode != 0:
            stderr_text = apply_masks(
                stderr.decode(errors="replace"),
                masks=secret_projection.mask_values,
            )
            logger.error(
                "Subprocess failed",
                action=input.task.action,
                returncode=proc.returncode,
                stderr=stderr_text,
            )
            return ExecutorActionErrorInfo(
                type="SubprocessError",
                message=f"Subprocess exited with code {proc.returncode}: {stderr_text[:500]}",
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        # Parse result from stdout
        try:
            result_data = orjson.loads(stdout)
        except orjson.JSONDecodeError as e:
            logger.error(
                "Failed to parse subprocess output",
                action=input.task.action,
                stdout=stdout.decode()[:500],
                error=str(e),
            )
            return ExecutorActionErrorInfo(
                type="ProtocolError",
                message=f"Failed to parse subprocess output: {e}",
                action_name=input.task.action,
                filename="<subprocess>",
                function="execute_action",
            )

        # Handle success or error
        if result_data.get("success"):
            return result_data["result"]

        # Reconstruct error info
        error_data = result_data.get("error")
        if error_data:
            return ExecutorActionErrorInfo.model_validate(
                apply_masks_object(error_data, masks=secret_projection.mask_values)
            )

        return ExecutorActionErrorInfo(
            type="UnknownError",
            message="Subprocess returned neither success nor error",
            action_name=input.task.action,
            filename="<subprocess>",
            function="execute_action",
        )


# Lazy singleton - no lifespan required
_action_runner: ActionRunner | None = None


def get_action_runner() -> ActionRunner:
    """Get the global ActionRunner instance (lazy initialized)."""
    global _action_runner
    if _action_runner is None:
        _action_runner = ActionRunner()
    return _action_runner
