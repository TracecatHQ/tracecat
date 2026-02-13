"""In-process executor backend for tests only.

This backend executes actions directly in the current process without
any sandbox isolation.

WARNING: This backend provides NO isolation between actions. Actions share the
same process memory space, environment variables, and can affect each other's
state. NEVER use in production.

This backend exists solely for:
- Running unit/integration tests without subprocess overhead
- Local development when testing action logic
"""

from __future__ import annotations

import asyncio
import sys
import threading
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


_SYS_PATH_LOCK = threading.Lock()
_SYS_PATH_REF_COUNTS: dict[str, int] = {}
_MANAGED_SYS_PATH_ENTRIES: set[str] = set()


@contextmanager
def _temporary_sys_path(paths: list[str]) -> Iterator[None]:
    """Temporarily add paths to sys.path for module imports.

    This is used by TestBackend to make custom registry modules
    available during in-process execution.
    """
    if not paths:
        yield
        return

    unique_paths = list(dict.fromkeys(paths))

    with _SYS_PATH_LOCK:
        for path in reversed(unique_paths):
            ref_count = _SYS_PATH_REF_COUNTS.get(path, 0)
            if ref_count == 0 and path not in sys.path:
                sys.path.insert(0, path)
                _MANAGED_SYS_PATH_ENTRIES.add(path)
                logger.debug("Added path to sys.path", path=path)
            _SYS_PATH_REF_COUNTS[path] = ref_count + 1

    try:
        yield
    finally:
        with _SYS_PATH_LOCK:
            for path in unique_paths:
                ref_count = _SYS_PATH_REF_COUNTS.get(path, 0)
                if ref_count <= 1:
                    _SYS_PATH_REF_COUNTS.pop(path, None)
                    if path in _MANAGED_SYS_PATH_ENTRIES and path in sys.path:
                        sys.path.remove(path)
                        logger.debug("Removed path from sys.path", path=path)
                    _MANAGED_SYS_PATH_ENTRIES.discard(path)
                else:
                    _SYS_PATH_REF_COUNTS[path] = ref_count - 1


class TestBackend(ExecutorBackend):
    """In-process execution backend for tests only."""

    async def _execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action directly in-process using pre-resolved context."""
        action_name = resolved_context.action_impl.action_name or input.task.action
        logger.debug(
            "Executing action in test backend (in-process)",
            action=action_name,
            task_ref=input.task.ref,
        )

        tarball_paths = await self._ensure_registry_tarballs(input, role)
        if tarball_paths:
            logger.debug(
                "Adding tarball paths to sys.path for test execution",
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
                "Test backend execution timed out",
                action=action_name,
                timeout=timeout,
            )
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="TimeoutError",
                message=f"Action execution timed out after {timeout}s",
                filename="test.py",
                function="execute",
            )
            return ExecutorResultFailure(error=error_info)
        except Exception as e:
            logger.error(
                "Test backend execution failed",
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
        """Execute action using pre-resolved context."""
        action_impl = resolved_context.action_impl
        if action_impl.type == "template":
            raise NotImplementedError(
                "Template actions must be orchestrated at the service layer. "
                "TestBackend should only receive UDF invocations."
            )

        action_name = action_impl.action_name or (
            f"{action_impl.module}.{action_impl.name}"
            if action_impl.module
            else action_impl.name or "unknown"
        )

        ctx_role.set(role)
        ctx_run.set(input.run_context)
        ctx_session_id.set(input.session_id)
        ctx_interaction.set(input.interaction_context)

        log = ctx_logger.get() or logger.bind(ref=input.task.ref)

        registry_ctx = RegistryContext(
            workspace_id=resolved_context.workspace_id,
            workflow_id=resolved_context.workflow_id,
            run_id=resolved_context.run_id,
            environment=input.run_context.environment,
            api_url=config.TRACECAT__API_URL,
            token=resolved_context.executor_token,
        )
        set_context(registry_ctx)

        fn = self._load_udf_callable(action_impl)

        log.info(
            "Run action",
            task_ref=input.task.ref,
            action_name=action_name,
        )

        flattened_secrets = secrets_manager.flatten_secrets(resolved_context.secrets)
        secrets_token = registry_secrets.set_context(flattened_secrets)

        try:
            args = resolved_context.evaluated_args or {}
            with secrets_manager.env_sandbox(flattened_secrets):
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(**args)
                else:
                    result = await asyncio.to_thread(fn, **args)

            log.trace("Result", result=result)
            return result
        finally:
            registry_secrets.reset_context(secrets_token)

    def _load_udf_callable(self, action_impl: ActionImplementation):
        """Load the UDF callable from action_impl metadata."""
        if not action_impl.module or not action_impl.name:
            raise ValueError(
                "UDF action missing module or name: "
                f"module={action_impl.module}, name={action_impl.name}"
            )

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
        """Download and extract registry tarballs, returning paths for sys.path."""
        if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            return []

        tarball_uris = await self._get_tarball_uris(input, role)
        if not tarball_uris:
            logger.debug("No tarball URIs found, using empty paths")
            return []

        runner = get_action_runner()
        extracted_paths: list[str] = []

        for tarball_uri in tarball_uris:
            try:
                extracted_path = await runner.ensure_registry_environment(tarball_uri)
                if extracted_path:
                    extracted_paths.append(str(extracted_path))
            except Exception as e:
                logger.warning(
                    "Failed to extract tarball for test execution",
                    tarball_uri=tarball_uri,
                    error=str(e),
                )

        logger.debug(
            "Extracted registry tarballs for test execution",
            count=len(extracted_paths),
        )
        return extracted_paths

    async def _get_tarball_uris(self, input: RunActionInput, role: Role) -> list[str]:
        """Get tarball URIs for registry environment (deterministic ordering)."""
        if role.organization_id is None:
            raise ValueError(
                "organization_id is required for registry artifacts lookup"
            )

        try:
            artifacts = await get_registry_artifacts_for_lock(
                input.registry_lock.origins, role.organization_id
            )
            return self._sort_tarball_uris(artifacts)
        except Exception as e:
            logger.warning(
                "Failed to load registry artifacts for test execution",
                error=str(e),
            )
            return []

    def _sort_tarball_uris(
        self, artifacts: list[RegistryArtifactsContext]
    ) -> list[str]:
        """Sort tarballs: tracecat_registry first, then lexicographically by origin."""
        builtin_uris: list[str] = []
        other_uris: list[tuple[str, str]] = []

        for artifact in artifacts:
            if not artifact.tarball_uri:
                continue
            if artifact.origin == DEFAULT_REGISTRY_ORIGIN:
                builtin_uris.append(artifact.tarball_uri)
            else:
                other_uris.append((artifact.origin, artifact.tarball_uri))

        other_uris.sort(key=lambda x: x[0])
        return builtin_uris + [uri for _, uri in other_uris]
