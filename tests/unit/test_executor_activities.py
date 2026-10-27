"""Tests for ExecutorActivities.

These tests cover the Temporal activity that handles action execution.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import HTTPClientError
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
)
from temporalio.exceptions import (
    CancelledError as TemporalCancelledError,
)

from tracecat import config
from tracecat.agent.executor.activity import (
    _resolve_and_probe_stdio_config,
    probe_stdio_mcp_connection_activity,
)
from tracecat.agent.mcp.activities import persist_stdio_mcp_connection_activity
from tracecat.agent.mcp.stdio_probe import NsjailStdioMCPRunner
from tracecat.agent.mcp.stdio_probe_types import (
    MCP_STDIO_PROBE_TIMEOUT_CAP,
    StdioMCPPersistInput,
    StdioMCPProbeInput,
    StdioMCPProbeResult,
)
from tracecat.agent.mcp.stdio_runner import StdioMCPServer
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.error_transport import (
    ActionErrorTransportDetail,
    parse_classified_action_error_payload,
)
from tracecat.dsl.schemas import (
    ActionStatement,
    RunActionInput,
    RunContext,
    StreamID,
)
from tracecat.exceptions import (
    EntitlementRequired,
    ExecutionError,
    LoopExecutionError,
    TracecatValidationError,
)
from tracecat.executor.activities import ExecutorActivities
from tracecat.executor.registry_artifacts import (
    RegistryArtifactExtractionError,
)
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.observability.otel import (
    initialize_platform_tracing,
    shutdown_platform_tracing,
)
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import (
    build_error_transport_detail,
    extract_error_classification,
)


@pytest.fixture(autouse=True)
def reset_platform_tracing(monkeypatch: pytest.MonkeyPatch):
    """Keep the process-global tracing runtime isolated between tests."""
    propagator = get_global_textmap()
    shutdown_platform_tracing()
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", False)
    yield
    shutdown_platform_tracing()
    set_global_textmap(propagator)


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def mock_run_action_input() -> RunActionInput:
    """Create a mock RunActionInput for testing."""
    wf_id = WorkflowUUID.new_uuid4()
    action_name = "core.http_request"
    return RunActionInput(
        task=ActionStatement(
            action=action_name,
            args={"url": "https://example.com"},
            ref="test_action",
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={action_name: "tracecat_registry"},
        ),
    )


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _stdio_probe_input(mcp_integration_id: uuid.UUID, role: Role) -> StdioMCPProbeInput:
    return StdioMCPProbeInput(mcp_integration_id=mcp_integration_id, role=role)


def _activity_error_from(cause: BaseException) -> ActivityError:
    try:
        raise ActivityError(
            "Synthetic activity cancellation",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test-executor",
            activity_type="execute_action_activity",
            activity_id="synthetic-activity-id",
            retry_state=None,
        ) from cause
    except ActivityError as error:
        return error


@pytest.fixture
def activity_mocks() -> Iterator[SimpleNamespace]:
    with (
        patch("tracecat.executor.activities.activity") as mock_activity,
        patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
        patch(
            "tracecat.executor.activities.dispatch_action",
            new_callable=AsyncMock,
        ) as mock_dispatch,
    ):
        mock_activity.info.return_value = MagicMock(attempt=1)
        mock_activity.heartbeat = MagicMock()
        mock_backend.return_value = MagicMock()
        yield SimpleNamespace(
            activity=mock_activity, backend=mock_backend, dispatch=mock_dispatch
        )


def _error_info(
    message: str, *, loop_iteration: int | None = None
) -> ExecutorActionErrorInfo:
    return ExecutorActionErrorInfo(
        type="ValueError",
        message=message,
        action_name="test_action",
        filename="<test>",
        function="test_function",
        loop_iteration=loop_iteration,
    )


class TestExecuteActionActivity:
    """Tests for the execute_action_activity function."""

    @pytest.mark.anyio
    async def test_successful_execution_emits_fine_grained_spans(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        monkeypatch: pytest.MonkeyPatch,
        activity_mocks: SimpleNamespace,
    ) -> None:
        """The action activity exposes input, runtime, and storage latency."""
        exporter = InMemorySpanExporter()
        monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
        runtime = initialize_platform_tracing("tracecat-executor", exporter=exporter)
        assert runtime is not None

        try:
            with patch(
                "tracecat.executor.activities.materialize_context",
                new_callable=AsyncMock,
            ) as mock_materialize:
                activity_mocks.activity.info.return_value = MagicMock(
                    attempt=1,
                    task_queue="shared-action-queue",
                )
                activity_mocks.dispatch.return_value = {"status": "success"}
                mock_materialize.return_value = mock_run_action_input.exec_context

                tracer = runtime.tracer("test.executor-activity")
                with tracer.start_as_current_span("temporal.activity"):
                    await ExecutorActivities.execute_action_activity(
                        mock_run_action_input, mock_role
                    )
        finally:
            shutdown_platform_tracing()

        spans = {span.name: span for span in exporter.get_finished_spans()}
        assert set(spans) == {
            "temporal.activity",
            "tracecat.action.materialize_inputs",
            "tracecat.action.execute",
            "tracecat.action.store_result",
        }
        activity_span = spans["temporal.activity"]
        assert activity_span.context is not None
        activity_span_id = activity_span.context.span_id
        for child_name in (
            "tracecat.action.materialize_inputs",
            "tracecat.action.execute",
            "tracecat.action.store_result",
        ):
            child_parent = spans[child_name].parent
            assert child_parent is not None
            assert child_parent.span_id == activity_span_id

        attributes = activity_span.attributes
        assert attributes is not None
        assert attributes["tracecat.organization.id"] == str(mock_role.organization_id)
        assert attributes["tracecat.workspace.id"] == str(mock_role.workspace_id)
        assert attributes["tracecat.workflow.id"] == str(
            mock_run_action_input.run_context.wf_id
        )
        assert attributes["tracecat.workflow.execution.id"] == str(
            mock_run_action_input.run_context.wf_exec_id
        )
        assert attributes["tracecat.action.ref"] == "test_action"
        assert attributes["tracecat.action.name"] == "core.http_request"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("make_error", "kind", "non_retryable", "has_diagnostic"),
        [
            pytest.param(
                lambda: ExecutionError(info=_error_info("Invalid argument")),
                RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                False,
                True,
                id="execution_error",
            ),
            pytest.param(
                lambda: LoopExecutionError(
                    [
                        ExecutionError(
                            info=_error_info("Loop iteration failed", loop_iteration=0)
                        )
                    ]
                ),
                RuntimeErrorKind.ACTION_EXECUTION_FAILED,
                False,
                False,
                id="loop_execution_error",
            ),
            pytest.param(
                lambda: RuntimeError("Unexpected crash"),
                RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
                True,
                False,
                id="unexpected_error",
            ),
            pytest.param(
                lambda: ApplicationError("Original error", type="CustomError"),
                RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
                False,
                False,
                id="application_error_passthrough",
            ),
        ],
    )
    async def test_dispatch_error_is_converted_to_application_error(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
        make_error: Callable[[], Exception],
        kind: RuntimeErrorKind,
        non_retryable: bool,
        has_diagnostic: bool,
    ) -> None:
        mock_run_action_input.stream_id = StreamID("test-stream-123")
        activity_mocks.dispatch.side_effect = make_error()

        with pytest.raises(ApplicationError) as exc_info:
            await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

        app_error = exc_info.value
        assert app_error.type == kind.value
        assert app_error.non_retryable is non_retryable
        if has_diagnostic:
            assert len(app_error.details) > 0
            transport = parse_classified_action_error_payload(app_error.details[0])
            assert isinstance(transport, ActionErrorTransportDetail)
            assert transport.diagnostic is not None
            assert transport.diagnostic.stream_id == "test-stream-123"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "cancellation",
        [
            pytest.param(
                TemporalCancelledError("activity cancelled"),
                id="direct-temporal-cancellation",
            ),
            pytest.param(
                _activity_error_from(TemporalCancelledError("activity cancelled")),
                id="wrapped-temporal-cancellation",
            ),
        ],
    )
    async def test_temporal_cancellation_is_not_classified(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
        cancellation: BaseException,
    ) -> None:
        activity_mocks.dispatch.side_effect = cancellation

        with pytest.raises(type(cancellation)) as exc_info:
            await ExecutorActivities.execute_action_activity(
                mock_run_action_input,
                mock_role,
            )

        assert exc_info.value is cancellation

    @pytest.mark.anyio
    async def test_executor_backend_initialization_failure_is_classified(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
    ) -> None:
        activity_mocks.backend.side_effect = RuntimeError("backend diagnostic")

        with pytest.raises(ApplicationError) as exc_info:
            await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert (
            classification.kind
            is RuntimeErrorKind.EXECUTOR_BACKEND_INITIALIZATION_FAILED
        )
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
        assert exc_info.value.type == classification.kind.value
        assert "backend diagnostic" not in exc_info.value.message
        activity_mocks.dispatch.assert_not_awaited()

    @pytest.mark.anyio
    async def test_result_persistence_transport_failure_is_classified(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
    ) -> None:
        storage = MagicMock()
        storage.store = AsyncMock(
            side_effect=HTTPClientError(error=RuntimeError("storage diagnostic"))
        )
        activity_mocks.dispatch.return_value = {"ok": True}
        with patch(
            "tracecat.executor.activities.get_object_storage",
            return_value=storage,
        ):
            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert (
            classification.kind
            is RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE
        )
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
        assert exc_info.value.type == classification.kind.value
        assert "storage diagnostic" not in exc_info.value.message

    @pytest.mark.anyio
    async def test_loop_platform_failure_preserves_origin_kind(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
    ) -> None:
        error_info = ExecutorActionErrorInfo(
            type="RegistryArtifactExtractionError",
            message="masked loop error",
            action_name="test_action",
            filename="<test>",
            function="test_function",
            loop_iteration=0,
        )
        iteration_error = ExecutionError(info=error_info)
        iteration_error.__cause__ = RegistryArtifactExtractionError()
        activity_mocks.dispatch.side_effect = LoopExecutionError([iteration_error])

        with pytest.raises(ApplicationError) as exc_info:
            await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert (
            classification.kind is RuntimeErrorKind.EXECUTOR_REGISTRY_EXTRACTION_FAILED
        )
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
        assert exc_info.value.type == classification.kind.value

    @pytest.mark.anyio
    async def test_entitlement_error_raises_non_retryable_application_error(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
    ) -> None:
        """Test that EntitlementRequired is converted to non-retryable ApplicationError."""
        activity_mocks.dispatch.side_effect = EntitlementRequired("custom_registry")

        with pytest.raises(ApplicationError) as exc_info:
            await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

        app_error = exc_info.value
        assert app_error.type == RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED.value
        assert app_error.non_retryable is True
        assert len(app_error.details) > 0
        transport = parse_classified_action_error_payload(app_error.details[0])
        assert isinstance(transport, ActionErrorTransportDetail)
        detail = transport.diagnostic
        assert detail is not None
        assert "custom_registry" in detail.message
        classification = extract_error_classification(app_error)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.USER
        assert classification.kind is RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED

    @pytest.mark.anyio
    async def test_non_retryable_application_error_passthrough_drops_retry_delay(
        self,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        activity_mocks: SimpleNamespace,
    ) -> None:
        classification = RuntimeErrorClassification.platform(
            kind=RuntimeErrorKind.EXECUTOR_REGISTRY_CAPACITY_EXHAUSTED,
            message="Tracecat executor artifact capacity is exhausted",
            retry_disposition=RetryDisposition.NON_RETRYABLE,
        )
        activity_mocks.dispatch.side_effect = ApplicationError(
            "Original error",
            build_error_transport_detail(classification).model_dump(mode="json"),
            type="CustomError",
            next_retry_delay=timedelta(seconds=1),
        )

        with pytest.raises(ApplicationError) as exc_info:
            await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

        app_error = exc_info.value
        assert extract_error_classification(app_error) == classification
        assert app_error.type == classification.kind.value
        assert app_error.non_retryable is True
        assert app_error.next_retry_delay is None


class TestProbeStdioMCPConnectionActivity:
    @staticmethod
    def _stdio_integration(mcp_integration_id: uuid.UUID) -> SimpleNamespace:
        return SimpleNamespace(
            id=mcp_integration_id,
            workspace_id=uuid.uuid4(),
            slug="test-stdio-server",
            name="Test stdio server",
            server_type="stdio",
            stdio_command="python",
            stdio_args=["-m", "example"],
            timeout=30,
        )

    @pytest.mark.anyio
    async def test_probe_lists_tools_through_installed_runner(
        self,
        mock_role: Role,
    ) -> None:
        """The activity hands the validated server to the installed runner."""
        integrations_svc = MagicMock()
        integrations_svc.validate_stdio_server_config.return_value = None
        preset_svc = MagicMock()
        preset_svc.role = mock_role
        expected = StdioMCPProbeResult(
            success=True,
            message="Connected successfully — 0 tools available",
        )
        runner = MagicMock()
        runner.list_tools = AsyncMock(return_value=expected)
        server = StdioMCPServer(
            workspace_id=uuid.uuid4(),
            mcp_integration_id=uuid.uuid4(),
            name="test-stdio-server",
            command="python",
            args=("-m", "example"),
        )

        with patch(
            "tracecat.agent.executor.activity.get_stdio_mcp_runner",
            return_value=runner,
        ):
            result = await _resolve_and_probe_stdio_config(
                preset_svc=preset_svc,
                integrations_svc=integrations_svc,
                server=server,
                stdio_env=None,
            )

        assert result is expected
        runner.list_tools.assert_awaited_once_with(server, None)

    @pytest.mark.anyio
    @pytest.mark.parametrize("fails_by", ["result", "exception"])
    async def test_probe_redacts_env_from_runner_failures(
        self,
        mock_role: Role,
        fails_by: str,
    ) -> None:
        """A replacement runner's failure text never carries the resolved env."""
        integrations_svc = MagicMock()
        integrations_svc.validate_stdio_server_config.return_value = None
        preset_svc = MagicMock()
        preset_svc.role = mock_role
        stdio_env = {"API_TOKEN": "super-secret-value"}
        preset_svc.resolve_stdio_env = AsyncMock(return_value=stdio_env)
        runner = MagicMock()
        leaked = "server rejected token super-secret-value"
        if fails_by == "result":
            runner.list_tools = AsyncMock(
                return_value=StdioMCPProbeResult(
                    success=False, message=leaked, error=leaked
                )
            )
        else:
            runner.list_tools = AsyncMock(side_effect=RuntimeError(leaked))

        with patch(
            "tracecat.agent.executor.activity.get_stdio_mcp_runner",
            return_value=runner,
        ):
            result = await _resolve_and_probe_stdio_config(
                preset_svc=preset_svc,
                integrations_svc=integrations_svc,
                server=StdioMCPServer(
                    workspace_id=uuid.uuid4(),
                    mcp_integration_id=uuid.uuid4(),
                    name="test-stdio-server",
                    command="python",
                ),
                stdio_env=stdio_env,
            )

        assert result.success is False
        assert "super-secret-value" not in result.model_dump_json()
        assert result.error is not None and "[redacted]" in result.error

    @pytest.mark.anyio
    async def test_default_runner_uses_verification_timeout(self) -> None:
        """The built-in runner passes the verification timeout to the probe."""
        expected = StdioMCPProbeResult(
            success=True,
            message="Connected successfully — 0 tools available",
        )
        server = StdioMCPServer(
            workspace_id=uuid.uuid4(),
            mcp_integration_id=uuid.uuid4(),
            name="test-stdio-server",
            command="python",
            args=("-m", "example"),
        )

        with patch(
            "tracecat.agent.mcp.stdio_probe.probe_stdio_mcp_tools_in_sandbox",
            new_callable=AsyncMock,
            return_value=expected,
        ) as probe_stdio:
            result = await NsjailStdioMCPRunner().list_tools(server, None)

        assert result is expected
        probe_stdio.assert_awaited_once_with(
            command="python",
            args=["-m", "example"],
            env=None,
            timeout=MCP_STDIO_PROBE_TIMEOUT_CAP,
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("resolve_error", "expected_error"),
        [
            (
                TracecatValidationError("invalid template references super-secret"),
                "invalid template references [redacted]",
            ),
            (
                ValueError("bad expression references super-secret"),
                "bad expression references [redacted]",
            ),
        ],
    )
    async def test_stdio_env_domain_error_returns_failed_probe_result(
        self,
        mock_role: Role,
        resolve_error: Exception,
        expected_error: str,
    ) -> None:
        mcp_integration_id = uuid.uuid4()
        integration = self._stdio_integration(mcp_integration_id)
        stdio_env = {"API_TOKEN": "super-secret"}
        preset_svc = SimpleNamespace(
            session=object(),
            role=mock_role,
            resolve_stdio_env=AsyncMock(side_effect=resolve_error),
        )

        class FakeIntegrationService:
            def __init__(self, session: object, *, role: Role) -> None:
                del session, role

            async def get_mcp_integration(
                self, *, mcp_integration_id: uuid.UUID
            ) -> SimpleNamespace:
                assert mcp_integration_id == integration.id
                return integration

            def decrypt_stdio_env(
                self, mcp_integration: SimpleNamespace
            ) -> dict[str, str]:
                assert mcp_integration is integration
                return stdio_env

            def validate_stdio_server_config(
                self,
                *,
                command: str | None,
                args: list[str] | None,
                env: dict[str, str] | None,
            ) -> None:
                del command, args, env

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.AgentPresetService.with_session",
                lambda *_, **__: _AsyncContext(preset_svc),
            ),
            patch(
                "tracecat.agent.executor.activity.IntegrationService",
                FakeIntegrationService,
            ),
            patch(
                "tracecat.agent.executor.activity.get_stdio_mcp_runner"
            ) as get_runner,
        ):
            mock_activity.heartbeat = MagicMock()

            result = await probe_stdio_mcp_connection_activity(
                _stdio_probe_input(mcp_integration_id, mock_role)
            )

        assert result.success is False
        assert (
            result.message == "MCP integration stdio environment could not be resolved"
        )
        assert result.error == expected_error
        preset_svc.resolve_stdio_env.assert_awaited_once_with(
            stdio_env=stdio_env,
            mcp_integration_id=integration.id,
            mcp_integration_slug=integration.slug,
        )
        get_runner.assert_not_called()

    @pytest.mark.anyio
    async def test_stdio_env_unexpected_error_propagates(
        self,
        mock_role: Role,
    ) -> None:
        mcp_integration_id = uuid.uuid4()
        integration = self._stdio_integration(mcp_integration_id)
        stdio_env = {"API_TOKEN": "super-secret"}
        unexpected_error = RuntimeError("secret store unavailable")
        preset_svc = SimpleNamespace(
            session=object(),
            role=mock_role,
            resolve_stdio_env=AsyncMock(side_effect=unexpected_error),
        )

        class FakeIntegrationService:
            def __init__(self, session: object, *, role: Role) -> None:
                del session, role

            async def get_mcp_integration(
                self, *, mcp_integration_id: uuid.UUID
            ) -> SimpleNamespace:
                assert mcp_integration_id == integration.id
                return integration

            def decrypt_stdio_env(
                self, mcp_integration: SimpleNamespace
            ) -> dict[str, str]:
                assert mcp_integration is integration
                return stdio_env

            def validate_stdio_server_config(
                self,
                *,
                command: str | None,
                args: list[str] | None,
                env: dict[str, str] | None,
            ) -> None:
                del command, args, env

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.AgentPresetService.with_session",
                lambda *_, **__: _AsyncContext(preset_svc),
            ),
            patch(
                "tracecat.agent.executor.activity.IntegrationService",
                FakeIntegrationService,
            ),
            patch(
                "tracecat.agent.executor.activity.get_stdio_mcp_runner"
            ) as get_runner,
        ):
            mock_activity.heartbeat = MagicMock()

            with pytest.raises(RuntimeError) as exc_info:
                await probe_stdio_mcp_connection_activity(
                    _stdio_probe_input(mcp_integration_id, mock_role)
                )

        assert exc_info.value is unexpected_error
        preset_svc.resolve_stdio_env.assert_awaited_once_with(
            stdio_env=stdio_env,
            mcp_integration_id=integration.id,
            mcp_integration_slug=integration.slug,
        )
        get_runner.assert_not_called()

    @pytest.mark.anyio
    async def test_persist_activity_persists_merged_tools(
        self,
        mock_role: Role,
    ) -> None:
        mcp_integration_id = uuid.uuid4()
        discovered_tools = [
            MCPToolSummary(name="search", description="New search"),
            MCPToolSummary(name="create", description="Create issue"),
        ]
        persisted_tools = [
            MCPToolSummary(
                name="search",
                description="New search",
                enabled=False,
                requires_approval=True,
            ),
            MCPToolSummary(name="create", description="Create issue"),
        ]

        class FakeIntegrationService:
            def __init__(self) -> None:
                self.persist_mcp_integration_tools = AsyncMock(
                    return_value=persisted_tools
                )

        fake_svc = FakeIntegrationService()

        with (
            patch("tracecat.agent.mcp.activities.activity") as mock_activity,
            patch(
                "tracecat.agent.mcp.activities.IntegrationService.with_session",
                lambda *_, **__: _AsyncContext(fake_svc),
            ),
        ):
            mock_activity.heartbeat = MagicMock()
            result = await persist_stdio_mcp_connection_activity(
                StdioMCPPersistInput(
                    mcp_integration_id=mcp_integration_id,
                    role=mock_role,
                    tools=discovered_tools,
                )
            )

        assert result.success is True
        assert result.message == "Connected successfully — 2 tools available"
        assert result.tools == persisted_tools
        fake_svc.persist_mcp_integration_tools.assert_awaited_once_with(
            mcp_integration_id=mcp_integration_id,
            discovered_tools=discovered_tools,
        )
