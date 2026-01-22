"""Direct in-process executor backend for TESTING ONLY.

This backend executes actions directly in the current process without
any sandbox isolation.

WARNING: This backend provides NO isolation between actions. Actions share the
same process memory space, environment variables, and can affect each other's
state. NEVER use in production - use pool or ephemeral backends instead.

This backend exists solely for:
- Running unit/integration tests without subprocess overhead
- Local development when testing action logic
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from tracecat_registry import secrets as registry_secrets
from tracecat_registry.context import RegistryContext, set_context

from tracecat import config
from tracecat.contexts import (
    ctx_interaction,
    ctx_logger,
    ctx_role,
    ctx_run,
    ctx_session_id,
)
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.service import (
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionUDFImpl
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.loaders import load_udf_impl
from tracecat.secrets import secrets_manager

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput


@contextmanager
def _temporary_sys_path(paths: list[str]) -> Iterator[None]:
    """Temporarily add paths to sys.path for module imports.

    This is used by DirectBackend to make custom registry modules
    available during in-process execution.
    """
    if not paths:
        yield
        return

    # Add paths to the front of sys.path
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
            logger.debug("Added path to sys.path", path=path)

    try:
        yield
    finally:
        # Remove the paths we added
        for path in paths:
            if path in sys.path:
                sys.path.remove(path)
                logger.debug("Removed path from sys.path", path=path)


class DirectBackend(ExecutorBackend):
    """Direct in-process execution backend for TESTING ONLY.

    Executes actions directly in the current process without subprocess
    overhead. This provides the fastest execution for tests.

    WARNING: NEVER use in production. This backend provides NO isolation:
    - Actions share the same process memory space
    - Environment variables can leak between actions
    - No resource limits or sandboxing
    - A crash in one action affects the entire worker

    For production use pool (single-tenant) or ephemeral (multi-tenant).
    """

    async def _execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action directly in-process using pre-resolved context.

        Uses the same resolution flow as other backends - secrets and args
        are pre-resolved by invoke_once before calling execute.
        """

        action_name = resolved_context.action_impl.action_name or input.task.action
        logger.debug(
            "Executing action directly (no sandbox)",
            action=action_name,
            task_ref=input.task.ref,
        )

        # Download and extract tarballs for custom registries
        tarball_paths = await self._ensure_registry_tarballs(input, role)

        if tarball_paths:
            logger.debug(
                "Adding tarball paths to sys.path for direct execution",
                paths=tarball_paths,
            )

        try:
            with _temporary_sys_path(tarball_paths):
                result = await asyncio.wait_for(
                    self._execute_with_context(input, role, resolved_context),
                    timeout=timeout,
                )
            return ExecutorResultSuccess(result=result)
        except TimeoutError:
            logger.error(
                "Direct execution timed out",
                action=action_name,
                timeout=timeout,
            )
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="TimeoutError",
                message=f"Action execution timed out after {timeout}s",
                filename="direct.py",
                function="execute",
            )
            return ExecutorResultFailure(error=error_info)
        except Exception as e:
            logger.error(
                "Direct execution failed",
                action=action_name,
                error=str(e),
                error_type=type(e).__name__,
            )
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
            return ExecutorResultFailure(error=error_info)

    async def _execute_with_context(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
    ) -> Any:
        """Execute action using pre-resolved context.

        Uses resolved_context.action_impl to determine what to execute.
        This allows the backend to execute any action, not just the one
        specified in input.task.action (important for template step execution).

        NOTE: This backend executes UDFs directly without DB lookup.
        Templates are orchestrated by the service layer (_execute_template_action);
        only UDF leaf nodes reach this backend.
        """
        action_impl = resolved_context.action_impl

        # Templates should be orchestrated at the service layer
        # This backend should only receive UDF invocations
        if action_impl.type == "template":
            raise NotImplementedError(
                "Template actions must be orchestrated at the service layer. "
                "DirectBackend should only receive UDF invocations."
            )

        # Prefer registry action name for logging when available.
        action_name = action_impl.action_name or (
            f"{action_impl.module}.{action_impl.name}"
            if action_impl.module
            else action_impl.name or "unknown"
        )

        # Set context variables (matches untrusted_runner.py)
        ctx_role.set(role)
        ctx_run.set(input.run_context)
        ctx_session_id.set(input.session_id)
        # Always set interaction context (even if None) to prevent stale context leakage
        ctx_interaction.set(input.interaction_context)

        log = ctx_logger.get() or logger.bind(ref=input.task.ref)

        # Set up registry context for SDK access within UDFs
        registry_ctx = RegistryContext(
            workspace_id=resolved_context.workspace_id,
            workflow_id=resolved_context.workflow_id,
            run_id=resolved_context.run_id,
            environment=input.run_context.environment,
            api_url=config.TRACECAT__API_URL,
            token=resolved_context.executor_token,
        )
        set_context(registry_ctx)

        # Load UDF directly from module/name (no DB lookup)
        fn = self._load_udf_callable(action_impl)

        log.info(
            "Run action",
            task_ref=input.task.ref,
            action_name=action_name,
        )

        # Flatten secrets for env sandbox
        flattened_secrets = secrets_manager.flatten_secrets(resolved_context.secrets)

        # Initialize registry secrets context for SDK mode
        secrets_token = registry_secrets.set_context(flattened_secrets)

        try:
            # Execute with secrets in environment
            args = resolved_context.evaluated_args or {}
            with secrets_manager.env_sandbox(flattened_secrets):
                # Execute the UDF directly
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(**args)
                else:
                    result = await asyncio.to_thread(fn, **args)

            log.trace("Result", result=result)
            return result
        finally:
            # Reset secrets context to prevent leakage
            registry_secrets.reset_context(secrets_token)

    def _load_udf_callable(self, action_impl: ActionImplementation):
        """Load the UDF callable from action_impl metadata.

        Uses the module and name from action_impl to import and return
        the function, without any DB lookup. The origin is used for
        local-reload behavior when TRACECAT__LOCAL_REPOSITORY_ENABLED is set.
        """
        if not action_impl.module or not action_impl.name:
            raise ValueError(
                "UDF action missing module or name: "
                f"module={action_impl.module}, name={action_impl.name}"
            )

        # Create a RegistryActionUDFImpl to use with load_udf_impl
        # The url field is required but only used for logging; use origin
        udf_impl = RegistryActionUDFImpl(
            type="udf",
            url=action_impl.origin or "unknown",
            module=action_impl.module,
            name=action_impl.name,
        )
        return load_udf_impl(udf_impl)

    async def _ensure_registry_tarballs(
        self, input: RunActionInput, role: Role
    ) -> list[str]:
        """Download and extract registry tarballs, returning paths to add to sys.path.

        This ensures custom registry modules are available for in-process execution.
        Uses ActionRunner to handle downloading and caching.

        Args:
            input: The RunActionInput containing registry_lock with origin versions.

        Returns:
            List of extracted tarball directory paths to add to sys.path.
        """
        if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            return []

        # Get tarball URIs for all origins in the registry lock
        tarball_uris = await self._get_tarball_uris(input, role)
        if not tarball_uris:
            logger.debug("No tarball URIs found, using empty paths")
            return []

        # Download and extract each tarball using ActionRunner
        runner = get_action_runner()
        extracted_paths: list[str] = []

        for tarball_uri in tarball_uris:
            try:
                extracted_path = await runner.ensure_registry_environment(tarball_uri)
                if extracted_path:
                    extracted_paths.append(str(extracted_path))
            except Exception as e:
                logger.warning(
                    "Failed to extract tarball for direct execution",
                    tarball_uri=tarball_uri,
                    error=str(e),
                )

        logger.debug(
            "Extracted registry tarballs for direct execution",
            count=len(extracted_paths),
        )
        return extracted_paths

    async def _get_tarball_uris(self, input: RunActionInput, role: Role) -> list[str]:
        """Get tarball URIs for registry environment (deterministic ordering).

        Args:
            input: The RunActionInput containing task and execution context

        Returns:
            List of tarball URIs in deterministic order (tracecat_registry first,
            then lexicographically by origin).
        """
        try:
            artifacts = await get_registry_artifacts_for_lock(
                input.registry_lock.origins, role.organization_id
            )
            return self._sort_tarball_uris(artifacts)
        except Exception as e:
            logger.warning(
                "Failed to load registry artifacts for direct execution",
                error=str(e),
            )
            return []

    def _sort_tarball_uris(
        self, artifacts: list[RegistryArtifactsContext]
    ) -> list[str]:
        """Sort tarballs: tracecat_registry first, then lexicographically by origin."""
        builtin_uris: list[str] = []
        other_uris: list[tuple[str, str]] = []  # (origin, uri)

        for artifact in artifacts:
            if not artifact.tarball_uri:
                continue
            if artifact.origin == DEFAULT_REGISTRY_ORIGIN:
                builtin_uris.append(artifact.tarball_uri)
            else:
                other_uris.append((artifact.origin, artifact.tarball_uri))

        # Sort non-builtin by origin lexicographically
        other_uris.sort(key=lambda x: x[0])

        return builtin_uris + [uri for _, uri in other_uris]
