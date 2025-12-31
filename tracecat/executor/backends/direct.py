"""Direct subprocess executor backend.

This backend executes actions in a subprocess without nsjail sandbox isolation.
It is intended for development and testing only.

WARNING: Do not use in production with untrusted workloads.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import TYPE_CHECKING

import orjson
from pydantic_core import to_json

from tracecat import config
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
)
from tracecat.executor.service import (
    get_registry_artifacts_cached,
    get_registry_artifacts_for_lock,
)
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput


class DirectBackend(ExecutorBackend):
    """Direct subprocess execution backend.

    Executes actions in a subprocess with PYTHONPATH set to include
    registry tarballs. No nsjail sandbox isolation is applied.

    Use cases:
    - Local development
    - Testing
    - Trusted single-tenant environments

    WARNING: Not suitable for production multitenant deployments.
    """

    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action in a subprocess with PYTHONPATH configured.

        For custom registry actions, this method ensures registry tarballs
        are downloaded/extracted and sets PYTHONPATH in the subprocess
        environment so custom modules can be imported.
        """
        action_name = input.task.action
        logger.debug(
            "Executing action in subprocess (no sandbox)",
            action=action_name,
            task_ref=input.task.ref,
        )

        # Get registry paths for custom registry modules
        registry_paths = await self._get_registry_paths(input, role)

        # Build environment with PYTHONPATH
        env = os.environ.copy()
        if registry_paths:
            pythonpath = ":".join(registry_paths)
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{pythonpath}:{existing}" if existing else pythonpath
            logger.debug("Set PYTHONPATH for subprocess", paths=registry_paths)

        # Prepare input JSON for subprocess
        input_json = to_json({"input": input, "role": role})

        logger.debug(
            "Executing action in subprocess",
            action=action_name,
            timeout=timeout,
        )

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tracecat.executor.subprocess_entrypoint",
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
                action=action_name,
                elapsed_ms=f"{elapsed_ms:.1f}",
                returncode=proc.returncode,
            )
        except TimeoutError:
            logger.error(
                "Action execution timed out, killing subprocess",
                action=action_name,
                timeout=timeout,
            )
            proc.kill()
            await proc.wait()
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="TimeoutError",
                message=f"Action execution timed out after {timeout}s",
                filename="direct.py",
                function="execute",
            )
            return ExecutorResultFailure(error=error_info)

        # Check for subprocess crash
        if proc.returncode != 0:
            stderr_text = stderr.decode()
            logger.error(
                "Subprocess failed",
                action=action_name,
                returncode=proc.returncode,
                stderr=stderr_text,
            )
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="SubprocessError",
                message=f"Subprocess exited with code {proc.returncode}: {stderr_text[:500]}",
                filename="direct.py",
                function="execute",
            )
            return ExecutorResultFailure(error=error_info)

        # Parse result from stdout
        try:
            result_data = orjson.loads(stdout)
        except orjson.JSONDecodeError as e:
            logger.error(
                "Failed to parse subprocess output",
                action=action_name,
                stdout=stdout.decode()[:500],
                error=str(e),
            )
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="ProtocolError",
                message=f"Failed to parse subprocess output: {e}",
                filename="direct.py",
                function="execute",
            )
            return ExecutorResultFailure(error=error_info)

        # Handle error response from subprocess
        if "error" in result_data:
            error_info = ExecutorActionErrorInfo.model_validate(result_data["error"])
            return ExecutorResultFailure(error=error_info)

        return ExecutorResultSuccess(result=result_data.get("result"))

    async def _get_registry_paths(self, input: RunActionInput, role: Role) -> list[str]:
        """Get extracted registry tarball paths for PYTHONPATH.

        Downloads and extracts registry tarballs if needed, returning
        the list of paths to add to PYTHONPATH.
        """
        if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            return []

        # Get tarball URIs from registry
        tarball_uris: list[str] = []
        try:
            if input.registry_lock:
                artifacts = await get_registry_artifacts_for_lock(input.registry_lock)
            else:
                artifacts = await get_registry_artifacts_cached(role)

            for artifact in artifacts:
                if artifact.tarball_uri:
                    tarball_uris.append(artifact.tarball_uri)
        except Exception as e:
            logger.warning(
                "Failed to load registry artifacts for direct execution",
                error=str(e),
            )
            return []

        if not tarball_uris:
            return []

        # Ensure all tarballs are extracted and collect paths
        runner = get_action_runner()
        paths: list[str] = []
        for tarball_uri in tarball_uris:
            target_dir = await runner.ensure_registry_environment(
                tarball_uri=tarball_uri
            )
            if target_dir:
                paths.append(str(target_dir))

        return paths
