"""Direct subprocess executor backend.

This backend executes actions in a fresh subprocess per invocation without
nsjail sandboxing. It avoids shared in-process import state while keeping
lower setup complexity than sandboxed backends.

For full isolation, use the ephemeral backend.
"""

from __future__ import annotations

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.logger import logger


class DirectBackend(EphemeralBackend):
    """Direct subprocess backend (one subprocess per action)."""

    async def _execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action in a direct subprocess (no nsjail forcing)."""
        action_name = input.task.action

        logger.debug(
            "Executing action in direct subprocess",
            action=action_name,
            task_ref=input.task.ref,
        )

        tarball_uris = await self._get_tarball_uris(input, role)

        if not tarball_uris and not config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            logger.error(
                "No registry tarballs resolved - cannot execute action",
                action=action_name,
                action_origin=resolved_context.action_impl.origin,
            )
            return ExecutorResultFailure(
                error=ExecutorActionErrorInfo(
                    type="RegistryError",
                    message="No registry tarballs available for execution. "
                    "Check that the registry is synced and the registry_lock is valid.",
                    action_name=action_name,
                    filename="<direct>",
                    function="execute",
                )
            )

        if tarball_uris:
            logger.debug("Using registry tarballs", count=len(tarball_uris))
        else:
            logger.debug("Using local repository mode, no tarballs mounted")

        runner = get_action_runner()
        result = await runner.execute_action(
            input=input,
            role=role,
            resolved_context=resolved_context,
            tarball_uris=tarball_uris,
            timeout=timeout,
            force_sandbox=False,
        )

        if isinstance(result, ExecutorActionErrorInfo):
            return ExecutorResultFailure(error=result)
        return ExecutorResultSuccess(result=result)
