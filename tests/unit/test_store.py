from collections.abc import AsyncGenerator

import aiobotocore.session
import pytest
from moto.server import ThreadedMotoServer

from tracecat.dsl.models import TaskResultDict
from tracecat.ee.store.models import (
    ActionResultHandle,
    WorkflowResultHandle,
)
from tracecat.ee.store.service import MinioStore
from tracecat.types.exceptions import StoreError


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
        expected_path = "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/_result.json"
        assert handle.to_key() == expected_path

    def test_from_path_valid(self, workflow_exec_id: str) -> None:
        """Test creating WorkflowResultHandle from valid path"""
        path = "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/_result.json"
        handle = WorkflowResultHandle.from_key(path)
        assert handle.wf_exec_id == workflow_exec_id

    @pytest.mark.parametrize(
        "invalid_path",
        [
            pytest.param(
                "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/wrong_name.json",
                id="wrong_filename",
            ),
            pytest.param(
                "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000",
                id="missing_filename",
            ),
            pytest.param(
                "workflows/wf-00000000000000000000000000000000/executions/_result.json",
                id="missing_exec_id",
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
        expected_path = "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/action789.json"
        assert handle.to_key() == expected_path

    def test_to_path_custom_ext(self, workflow_exec_id: str, action_ref: str) -> None:
        """Test converting ActionRefHandle to path with custom extension"""
        handle = ActionResultHandle(wf_exec_id=workflow_exec_id, ref=action_ref)
        expected_path = "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/action789.yaml"
        assert handle.to_key(ext="yaml") == expected_path

    def test_from_path_valid(self, workflow_exec_id: str, action_ref: str) -> None:
        """Test creating ActionRefHandle from valid path"""
        path = "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/action789.json"
        handle = ActionResultHandle.from_key(path)
        assert handle.wf_exec_id == workflow_exec_id
        assert handle.ref == action_ref

    def test_from_path_different_extension(
        self, workflow_exec_id: str, action_ref: str
    ) -> None:
        """Test creating ActionRefHandle from path with different extension"""
        path = "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000/action789.yaml"
        handle = ActionResultHandle.from_key(path)
        assert handle.wf_exec_id == workflow_exec_id
        assert handle.ref == action_ref

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param(
                "workflows/wf-00000000000000000000000000000000/executions/exec-00000000000000000000000000000000",
                id="missing_action_ref",
            ),
            pytest.param(
                "workflows/wf-00000000000000000000000000000000/executions/action789.json",
                id="missing_exec_id",
            ),
            pytest.param(
                "workflows/exec-00000000000000000000000000000000/action789.json",
                id="missing_workflow_id",
            ),
        ],
    )
    def test_from_path_invalid_format(self, path: str) -> None:
        """Test creating ActionRefHandle from invalid path format"""
        with pytest.raises(ValueError, match="Invalid path format"):
            ActionResultHandle.from_key(path)


@pytest.fixture(scope="module")
def moto_server():
    """Fixture to run a mocked AWS server for testing."""

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=9321)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def aws_credentials(monkeysession):
    """Mocked AWS Credentials for moto"""
    credentials = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }

    for key, value in credentials.items():
        monkeysession.setenv(key, value)


def test_minio_store(mock_store: MinioStore):
    """Test that MinioStore is correctly initialized with mocked client"""
    # Verify store instance is created
    assert mock_store is not None

    # Verify store configuration
    assert mock_store._endpoint_url == "http://127.0.0.1:9321"
    assert mock_store._session is not None
    assert mock_store._session.client is not None


@pytest.fixture(scope="function")
def mock_store(moto_server):
    """Create a MinioStore instance configured to use the moto server"""
    return MinioStore(
        endpoint_url=moto_server,  # Use the moto_server URL directly
        region="us-east-1",
        access_key="testing",
        secret_key="testing",
        bucket_name="test-bucket",
    )


@pytest.fixture
async def s3_client(moto_server):
    session = aiobotocore.session.get_session()
    async with session.create_client(
        "s3",
        endpoint_url=moto_server,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ) as client:
        yield client


@pytest.fixture
async def test_bucket(s3_client):
    bucket_name = "test-bucket"
    await s3_client.create_bucket(Bucket=bucket_name)
    yield bucket_name


@pytest.fixture
async def populated_store(
    mock_store: MinioStore, test_bucket: str
) -> AsyncGenerator[MinioStore, None]:
    """Fixture that provides a MinioStore with some pre-populated test data"""
    test_data = {
        "test-key-1": {"value": "data1"},
        "test-key-2": {"value": "data2"},
    }

    for key, data in test_data.items():
        await mock_store.put_json(bucket_name=test_bucket, key=key, data=data)

    yield mock_store


class TestMinioStore:
    """Test suite for MinioStore operations"""

    @pytest.mark.anyio
    async def test_put_json_success(
        self, mock_store: MinioStore, test_bucket: str
    ) -> None:
        """Test successful JSON object storage"""
        # Arrange
        test_data = {"key": "value"}
        test_key = "test-put-json"

        # Act
        result = await mock_store.put_json(
            bucket_name=test_bucket, key=test_key, data=test_data
        )

        # Assert
        assert result is not None
        stored_data = await mock_store.get_json(test_bucket, test_key)
        assert stored_data == test_data

    @pytest.mark.anyio
    async def test_get_json_success(
        self, populated_store: MinioStore, test_bucket: str
    ) -> None:
        """Test successful JSON object retrieval"""
        # Act
        result = await populated_store.get_json(test_bucket, "test-key-1")

        # Assert
        assert result == {"value": "data1"}

    @pytest.mark.anyio
    async def test_get_json_nonexistent_key(
        self, mock_store: MinioStore, test_bucket: str
    ) -> None:
        """Test retrieval of non-existent object"""
        # Act & Assert
        with pytest.raises(StoreError, match="Error retrieving JSON object"):
            await mock_store.get_json(test_bucket, "nonexistent-key")

    @pytest.mark.anyio
    async def test_get_json_batched(
        self, populated_store: MinioStore, test_bucket: str
    ) -> None:
        """Test batch retrieval of JSON objects"""
        # Arrange
        keys = ["test-key-1", "test-key-2"]

        # Act
        results = await populated_store.get_json_batched(test_bucket, keys)

        # Assert
        assert len(results) == 2
        assert results[0] == {"value": "data1"}
        assert results[1] == {"value": "data2"}

    @pytest.mark.anyio
    async def test_store_action_result(
        self, mock_store: MinioStore, workflow_exec_id: str
    ) -> None:
        """Test storing action result"""
        # Arrange
        action_ref = "test-action"
        action_result = TaskResultDict(result={"test": "data"}, result_typename="")

        # Act
        handle = await mock_store.store_action_result(
            execution_id=workflow_exec_id,
            action_ref=action_ref,
            action_result=action_result,
        )

        # Assert
        assert isinstance(handle, ActionResultHandle)
        stored_result = await mock_store.load_action_result(handle)
        assert stored_result == action_result

    @pytest.mark.anyio
    async def test_store_workflow_result(
        self, mock_store: MinioStore, workflow_exec_id: str
    ) -> None:
        """Test storing workflow result"""
        # Arrange
        workflow_result = TaskResultDict(
            result={"workflow": "complete"}, result_typename=""
        )

        # Act
        handle = await mock_store.store_workflow_result(
            execution_id=workflow_exec_id, workflow_result=workflow_result
        )

        # Assert
        assert isinstance(handle, WorkflowResultHandle)
        stored_result = await mock_store.load_task_result(handle)
        assert stored_result == workflow_result

    @pytest.mark.anyio
    async def test_load_action_result_batched(
        self, mock_store: MinioStore, workflow_exec_id: str
    ) -> None:
        """Test batch loading of action results"""
        # Arrange
        action_refs = ["action1", "action2"]
        action_results = {
            "action1": TaskResultDict(result={"data": "1"}, result_typename=""),
            "action2": TaskResultDict(result={"data": "2"}, result_typename=""),
        }

        # Store test data
        for ref, result in action_results.items():
            await mock_store.store_action_result(
                execution_id=workflow_exec_id, action_ref=ref, action_result=result
            )

        # Act
        results = await mock_store.load_action_result_batched(
            execution_id=workflow_exec_id, action_refs=action_refs
        )

        # Assert
        assert len(results) == 2
        assert all(ref in results for ref in action_refs)
        assert results["action1"] == action_results["action1"]
        assert results["action2"] == action_results["action2"]
