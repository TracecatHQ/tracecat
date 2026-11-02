"""Tests for WorkflowImportService folder helpers."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition


@pytest.fixture
def workflow_import_service() -> WorkflowImportService:
    session = AsyncMock()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )
    return WorkflowImportService(session=session, role=role)


@pytest.fixture
def sample_workflow() -> DSLInput:
    return DSLInput(
        title="Test Workflow",
        description="A test workflow",
        entrypoint=DSLEntrypoint(ref="start", expects={}),
        actions=[
            ActionStatement(
                ref="start",
                action="core.transform.passthrough",
                args={"value": "test"},
            )
        ],
    )


@pytest.fixture
def sample_remote_workflow(sample_workflow: DSLInput) -> RemoteWorkflowDefinition:
    return RemoteWorkflowDefinition(
        id=WorkflowUUID.new_uuid4().short(),
        alias="test-workflow",
        definition=sample_workflow,
    )


@pytest.fixture
def sample_remote_workflow_with_folder(
    sample_workflow: DSLInput,
) -> RemoteWorkflowDefinition:
    return RemoteWorkflowDefinition(
        id=WorkflowUUID.new_uuid4().short(),
        alias="test-workflow-with-folder",
        folder_path="/security/detections/",
        definition=sample_workflow,
    )


class TestWorkflowImportServiceFolders:
    """Tests for WorkflowImportService folder functionality."""

    @pytest.mark.anyio
    async def test_ensure_folder_exists_creates_nested_folders(
        self,
        workflow_import_service: WorkflowImportService,
    ) -> None:
        mock_folder_service = AsyncMock()
        workflow_import_service.folder_service = mock_folder_service

        mock_folder_service.get_folder_by_path.side_effect = [
            None,
            None,
            Mock(id=uuid.uuid4()),
        ]
        mock_security_folder = Mock(id=uuid.uuid4())
        mock_detections_folder = Mock(id=uuid.uuid4())
        mock_folder_service.create_folder.side_effect = [
            mock_security_folder,
            mock_detections_folder,
        ]

        await workflow_import_service._ensure_folder_exists("/security/detections/")

        assert mock_folder_service.create_folder.call_count == 2
        first_call = mock_folder_service.create_folder.call_args_list[0]
        assert first_call.kwargs["name"] == "security"
        assert first_call.kwargs["parent_path"] == "/"

        second_call = mock_folder_service.create_folder.call_args_list[1]
        assert second_call.kwargs["name"] == "detections"
        assert second_call.kwargs["parent_path"] == "/security/"
        mock_folder_service.get_folder_by_path.assert_called_with(
            "/security/detections/"
        )

    @pytest.mark.anyio
    async def test_ensure_folder_exists_with_existing_folders(
        self,
        workflow_import_service: WorkflowImportService,
    ) -> None:
        mock_folder_service = AsyncMock()
        workflow_import_service.folder_service = mock_folder_service

        mock_security_folder = Mock(id=uuid.uuid4())
        mock_detections_folder = Mock(id=uuid.uuid4())
        mock_folder_service.get_folder_by_path.side_effect = [
            mock_security_folder,
            None,
            mock_detections_folder,
        ]
        mock_folder_service.create_folder.return_value = mock_detections_folder

        await workflow_import_service._ensure_folder_exists("/security/detections/")

        assert mock_folder_service.create_folder.call_count == 1
        call_args = mock_folder_service.create_folder.call_args
        assert call_args.kwargs["name"] == "detections"
        assert call_args.kwargs["parent_path"] == "/security/"

    @staticmethod
    def _stub_create_dependencies(service: WorkflowImportService) -> Mock:
        mock_wf_mgmt = AsyncMock()
        mock_workflow = Mock()
        mock_workflow.id = uuid.uuid4()
        mock_wf_mgmt.create_db_workflow_from_dsl.return_value = mock_workflow
        service.wf_mgmt = mock_wf_mgmt
        service.session.flush = AsyncMock()
        service._ensure_folder_exists = AsyncMock(return_value=uuid.uuid4())
        service._create_schedules = AsyncMock()
        service._update_webhook = AsyncMock()
        service._update_case_trigger = AsyncMock()
        service._update_form = AsyncMock()
        service._create_tags = AsyncMock()
        return mock_workflow

    @staticmethod
    async def _create_new_workflow(
        service: WorkflowImportService,
        remote: RemoteWorkflowDefinition,
        *,
        sync_schedules: bool,
    ) -> None:
        mock_defn_service = AsyncMock()
        mock_defn_service.create_workflow_definition.return_value = Mock(version=1)
        with patch(
            "tracecat.workflow.store.import_service.WorkflowDefinitionsService",
            return_value=mock_defn_service,
        ):
            await service._create_new_workflow(remote, sync_schedules=sync_schedules)

    @pytest.mark.anyio
    @pytest.mark.parametrize("with_folder", [True, False])
    async def test_create_new_workflow_assigns_folder_from_path(
        self,
        workflow_import_service: WorkflowImportService,
        sample_remote_workflow: RemoteWorkflowDefinition,
        sample_remote_workflow_with_folder: RemoteWorkflowDefinition,
        with_folder: bool,
    ) -> None:
        mock_workflow = self._stub_create_dependencies(workflow_import_service)
        ensure_folder = cast(AsyncMock, workflow_import_service._ensure_folder_exists)
        remote = (
            sample_remote_workflow_with_folder
            if with_folder
            else sample_remote_workflow
        )

        await self._create_new_workflow(
            workflow_import_service, remote, sync_schedules=True
        )

        if with_folder:
            ensure_folder.assert_called_once_with("/security/detections/")
            assert mock_workflow.folder_id == ensure_folder.return_value
        else:
            ensure_folder.assert_not_called()

    @pytest.mark.anyio
    async def test_create_new_workflow_can_skip_schedule_sync(
        self,
        workflow_import_service: WorkflowImportService,
        sample_remote_workflow: RemoteWorkflowDefinition,
    ) -> None:
        self._stub_create_dependencies(workflow_import_service)

        await self._create_new_workflow(
            workflow_import_service, sample_remote_workflow, sync_schedules=False
        )

        cast(AsyncMock, workflow_import_service._create_schedules).assert_not_called()
