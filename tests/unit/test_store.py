import pytest

from tracecat.ee.store.models import (
    ActionResultHandle,
    WorkflowResultHandle,
)


@pytest.fixture
def workflow_id() -> str:
    return "wf-00000000000000000000000000000000"


@pytest.fixture
def exec_suffix() -> str:
    return "exec-00000000000000000000000000000000"


@pytest.fixture
def workflow_exec_id(workflow_id: str, exec_suffix: str) -> str:
    return f"{workflow_id}:{exec_suffix}"


class TestWorkflowResultHandle:
    """Tests for WorkflowResultHandle class"""

    def test_to_path(self, workflow_exec_id: str) -> None:
        """Test converting WorkflowResultHandle to path"""
        handle = WorkflowResultHandle(wf_exec_id=workflow_exec_id)
        expected_path = "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/_result.json"
        assert handle.to_key() == expected_path

    def test_from_path_valid(self, workflow_exec_id: str) -> None:
        """Test creating WorkflowResultHandle from valid path"""
        path = "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/_result.json"
        handle = WorkflowResultHandle.from_key(path)
        assert handle.wf_exec_id == workflow_exec_id

    @pytest.mark.parametrize(
        "invalid_path",
        [
            pytest.param(
                "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/wrong_name.json",
                id="wrong_filename",
            ),
            pytest.param(
                "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000",
                id="missing_filename",
            ),
            pytest.param(
                "wf-00000000000000000000000000000000/_result.json", id="missing_exec_id"
            ),
        ],
    )
    def test_from_path_invalid_format(self, invalid_path: str) -> None:
        """Test creating WorkflowResultHandle from invalid path format"""
        with pytest.raises(ValueError, match="Invalid path format"):
            WorkflowResultHandle.from_key(invalid_path)


class TestActionRefHandle:
    """Tests for ActionRefHandle class"""

    @pytest.fixture
    def action_ref(self) -> str:
        return "action789"

    def test_to_path_default_ext(self, workflow_exec_id: str, action_ref: str) -> None:
        """Test converting ActionRefHandle to path with default extension"""
        handle = ActionResultHandle(wf_exec_id=workflow_exec_id, ref=action_ref)
        expected_path = "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/action789.json"
        assert handle.to_key() == expected_path

    def test_to_path_custom_ext(self, workflow_exec_id: str, action_ref: str) -> None:
        """Test converting ActionRefHandle to path with custom extension"""
        handle = ActionResultHandle(wf_exec_id=workflow_exec_id, ref=action_ref)
        expected_path = "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/action789.yaml"
        assert handle.to_key(ext="yaml") == expected_path

    def test_from_path_valid(self, workflow_exec_id: str, action_ref: str) -> None:
        """Test creating ActionRefHandle from valid path"""
        path = "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/action789.json"
        handle = ActionResultHandle.from_key(path)
        assert handle.wf_exec_id == workflow_exec_id
        assert handle.ref == action_ref

    def test_from_path_different_extension(
        self, workflow_exec_id: str, action_ref: str
    ) -> None:
        """Test creating ActionRefHandle from path with different extension"""
        path = "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000/action789.yaml"
        handle = ActionResultHandle.from_key(path)
        assert handle.wf_exec_id == workflow_exec_id
        assert handle.ref == action_ref

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param(
                "wf-00000000000000000000000000000000/exec-00000000000000000000000000000000",
                id="missing_action_ref",
            ),
            pytest.param(
                "wf-00000000000000000000000000000000/action789.json",
                id="missing_exec_id",
            ),
            pytest.param(
                "/exec-00000000000000000000000000000000/action789.json",
                id="missing_workflow_id",
            ),
        ],
    )
    def test_from_path_invalid_format(self, path: str) -> None:
        """Test creating ActionRefHandle from invalid path format"""
        with pytest.raises(ValueError, match="Invalid path format"):
            ActionResultHandle.from_key(path)
