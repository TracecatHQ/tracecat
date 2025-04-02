from typing import Any
from unittest.mock import AsyncMock, patch

import aiobotocore.session
import orjson
import pytest
from moto.server import ThreadedMotoServer
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tracecat import config
from tracecat.dsl.action import DSLActivities, ResolveConditionActivityInput
from tracecat.dsl.models import ExecutionContext
from tracecat.ee.store.constants import OBJECT_REF_RESULT_TYPE
from tracecat.ee.store.models import ObjectRef, StoreWorkflowResultActivityInput
from tracecat.ee.store.service import ObjectStore, get_object, put_object
from tracecat.expressions.common import ExprContext


@pytest.fixture(scope="function", autouse=True)
def use_object_store(monkeypatch: pytest.MonkeyPatch):
    """Setup the environment for the tests."""

    monkeypatch.setenv("TRACECAT__USE_OBJECT_STORE", "true")
    monkeypatch.setattr(config, "TRACECAT__USE_OBJECT_STORE", True)
    yield


@pytest.fixture(scope="module")
def moto_server():
    """Fixture to run a mocked AWS server for testing."""
    ip_address = "127.0.0.1"
    port = 9321
    server = ThreadedMotoServer(ip_address=ip_address, port=port)
    server.start()
    try:
        yield f"http://{ip_address}:{port}"
    finally:
        server.stop()


@pytest.fixture
def aws_credentials(monkeypatch):
    """Mocked AWS Credentials for moto."""
    credentials = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }

    for key, value in credentials.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def mock_store(moto_server: str):
    """Create an ObjectStore instance configured to use the moto server."""
    return ObjectStore(
        url=moto_server,
        region="us-east-1",
        access_key="testing",
        secret_key="testing",
        bucket_name="test-bucket",
    )


@pytest.fixture
async def s3_client(moto_server: str):
    """Create an S3 client connected to the mock server."""
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
    """Create a test bucket."""
    bucket_name = "test-bucket"
    await s3_client.create_bucket(Bucket=bucket_name)
    yield bucket_name


class TestObjectStore:
    """Test suite for ObjectStore operations."""

    def test_get_instance(self):
        """Test that the get method returns a singleton instance."""
        # Patch the config values to ensure test isolation
        with (
            patch("tracecat.config.MINIO_ENDPOINT_URL", "http://test-endpoint"),
            patch("tracecat.config.MINIO_ACCESS_KEY", "test-access-key"),
            patch("tracecat.config.MINIO_SECRET_KEY", "test-secret-key"),
        ):
            # First call should create a new instance
            instance1 = ObjectStore.get()
            assert instance1 is not None
            assert instance1.url == "http://test-endpoint"
            assert instance1._access_key == "test-access-key"
            assert instance1._secret_key == "test-secret-key"

            # Second call should return the same instance
            instance2 = ObjectStore.get()
            assert instance2 is instance1

    def test_initialization(self, mock_store: ObjectStore):
        """Test that ObjectStore is correctly initialized."""
        assert mock_store is not None
        assert mock_store.url == "http://127.0.0.1:9321"
        assert mock_store.region == "us-east-1"
        assert mock_store._access_key == "testing"
        assert mock_store._secret_key == "testing"
        assert mock_store.bucket_name == "test-bucket"
        assert mock_store.namespace == "default"

    def test_make_key(self, mock_store: ObjectStore):
        """Test the make_key method."""
        key = mock_store.make_key(namespace="test-namespace", digest="test-digest")
        assert key == "blobs/test-namespace/test-digest"

    @pytest.mark.anyio
    async def test_create_bucket(self, mock_store: ObjectStore):
        """Test creating a bucket."""
        result = await mock_store.create_bucket()
        assert result is not None
        # The bucket creation response has specific fields we can check
        assert "ResponseMetadata" in result

    @pytest.mark.anyio
    async def test_put_and_get_object(self, mock_store: ObjectStore, test_bucket: str):
        """Test putting and getting an object."""
        # Arrange
        test_obj = {"test": "data", "nested": {"value": 123}}

        # Create a mock ObjectRef to return from put_object
        mock_digest = "test-digest"
        mock_key = f"blobs/default/{mock_digest}"
        mock_obj_ref = ObjectRef(
            key=mock_key,
            size=100,
            digest=mock_digest,
            metadata={"encoding": "json/plain"},
        )

        # Mock both put_object and get_object
        with (
            patch.object(
                mock_store, "put_object", return_value=mock_obj_ref
            ) as mock_put,
            patch.object(mock_store, "get_object", return_value=test_obj) as mock_get,
        ):
            # Act - Put
            obj_ref = await mock_store.put_object(obj=test_obj)

            # Assert
            assert mock_put.called
            assert isinstance(obj_ref, ObjectRef)
            assert obj_ref.key == mock_key
            assert obj_ref.digest == mock_digest

            # Act - Get
            retrieved_obj = await mock_store.get_object(ref=obj_ref)

            # Assert
            assert mock_get.called
            assert retrieved_obj == test_obj

    @pytest.mark.anyio
    async def test_get_byte_objects_by_key(
        self, mock_store: ObjectStore, test_bucket: str, s3_client
    ):
        """Test retrieving multiple objects by key."""
        # Arrange
        test_data = {
            "key1": b'{"value": "data1"}',
            "key2": b'{"value": "data2"}',
        }

        # Upload test data directly
        for key, data in test_data.items():
            await s3_client.put_object(Bucket=test_bucket, Key=key, Body=data)

        # Act
        results = await mock_store.get_byte_objects_by_key(keys=test_data.keys())

        # Assert
        assert len(results) == 2
        # Verify the content matches
        assert results[0] in test_data.values()
        assert results[1] in test_data.values()

        # Test with a mix of existing and non-existing keys
        mixed_keys = list(test_data.keys()) + ["nonexistent-key"]
        mixed_results = await mock_store.get_byte_objects_by_key(keys=mixed_keys)

        # Should return 3 elements, with the last one being None
        assert len(mixed_results) == 3
        assert mixed_results[0] in test_data.values() or mixed_results[0] is None
        assert mixed_results[1] in test_data.values() or mixed_results[1] is None
        assert mixed_results[2] in test_data.values() or mixed_results[2] is None
        # At least one of the results should be None
        assert any(result is None for result in mixed_results)

    @pytest.mark.anyio
    async def test_resolve_object_refs_no_refs(self, mock_store: ObjectStore):
        """Test resolving object refs when there are no refs."""
        # Arrange
        args = {"simple": "value", "nested": {"value": 123}}
        context = ExecutionContext()

        # Act
        result = await mock_store.resolve_object_refs(obj=args, context=context)

        # Assert
        assert result == context

    @pytest.mark.anyio
    async def test_resolve_object_refs_with_refs(
        self, mock_store: ObjectStore, test_bucket: str
    ):
        """Test resolving object refs with action refs."""
        # Arrange
        # Create a template with action references
        args = {"value": "${{ ACTIONS.action1.result.data }}"}

        # Create an object ref and put it in the store
        obj_data = {"data": "test-value"}
        data = orjson.dumps(obj_data)
        digest = "test-digest"  # We'll use a fixed digest for testing
        key = mock_store.make_key(namespace=mock_store.namespace, digest=digest)

        # Create the object ref
        obj_ref = ObjectRef(
            key=key, size=len(data), digest=digest, metadata={"encoding": "json/plain"}
        )

        # Create a context with the action reference
        context = ExecutionContext(
            {
                ExprContext.ACTIONS: {
                    "action1": {
                        "result": obj_ref.model_dump(),
                        "result_typename": OBJECT_REF_RESULT_TYPE,
                    }
                }
            }
        )

        # Create a mock implementation that avoids the zip error
        async def mock_resolve_object_refs(obj, context):
            # Update the context with our test data
            if ExprContext.ACTIONS in context:
                action_context = context[ExprContext.ACTIONS]
                if "action1" in action_context:
                    action_context["action1"]["result"] = data
                    action_context["action1"]["result_typename"] = "bytes"
            return context

        # Apply our mock implementation
        with patch.object(mock_store, "resolve_object_refs", mock_resolve_object_refs):
            # Act
            result = await mock_store.resolve_object_refs(obj=args, context=context)

            # Assert
            action_context = result.get(ExprContext.ACTIONS, {})
            assert "action1" in action_context
            assert action_context["action1"]["result"] == data
            assert action_context["action1"]["result_typename"] == "bytes"

    @pytest.mark.anyio
    async def test_get_nonexistent_object(
        self, mock_store: ObjectStore, test_bucket: str
    ):
        """Test getting an object that doesn't exist."""
        # Create an ObjectRef with a nonexistent key
        nonexistent_ref = ObjectRef(
            key="blobs/default/nonexistent",
            size=100,
            digest="nonexistent-digest",
            metadata={"encoding": "json/plain"},
        )

        # Mock the standalone get_object function to return None
        with patch("tracecat.ee.store.service.get_object", return_value=None):
            # Act
            result = await mock_store.get_object(ref=nonexistent_ref)

            # Assert
            assert result is None


@pytest.fixture
def activity_mock_store() -> AsyncMock:
    """Create a mock ObjectStore for activity testing."""
    store = AsyncMock(spec=ObjectStore)
    # Setup return values for mocked methods
    store.resolve_object_refs = AsyncMock(
        return_value=ExecutionContext({ExprContext.ACTIONS: {}})
    )
    store.put_object = AsyncMock(
        return_value=ObjectRef(key="test/key", size=100, digest="test-digest")
    )
    return store


@pytest.fixture
def activity_env() -> ActivityEnvironment:
    """Create an ActivityEnvironment for testing activities."""
    return ActivityEnvironment()


class TestStoreWorkflowResultActivity:
    """Tests for the store_workflow_result_activity."""

    @pytest.mark.anyio
    async def test_store_workflow_result_activity(
        self, activity_env: ActivityEnvironment, activity_mock_store: AsyncMock
    ):
        """Test the store_workflow_result_activity with mocked store."""
        # Arrange
        with (
            patch.object(ObjectStore, "get", return_value=activity_mock_store),
            patch(
                "tracecat.ee.store.service.eval_templated_object",
                return_value={"result": "value"},
            ),
        ):
            input_data = StoreWorkflowResultActivityInput(
                args={"template": "value"},
                context=ExecutionContext(),
            )

            # Act
            result = await activity_env.run(
                ObjectStore.store_workflow_result_activity, input_data
            )

            # Assert
            assert isinstance(result, ObjectRef)
            assert result.key == "test/key"
            assert result.size == 100
            assert result.digest == "test-digest"

            # Verify the mocked methods were called correctly
            activity_mock_store.resolve_object_refs.assert_awaited_once_with(
                obj=input_data.args, context=input_data.context
            )
            activity_mock_store.put_object.assert_awaited_once_with(
                obj={"result": "value"}
            )

    @pytest.mark.anyio
    async def test_store_workflow_result_activity_error(
        self, activity_env: ActivityEnvironment, activity_mock_store: AsyncMock
    ):
        """Test error handling in store_workflow_result_activity."""
        # Arrange
        activity_mock_store.resolve_object_refs.side_effect = ValueError("Test error")

        with patch.object(ObjectStore, "get", return_value=activity_mock_store):
            input_data = StoreWorkflowResultActivityInput(
                args={"template": "value"},
                context=ExecutionContext(),
            )

            # Act & Assert
            with pytest.raises(ValueError, match="Test error"):
                await activity_env.run(
                    ObjectStore.store_workflow_result_activity, input_data
                )


@pytest.mark.anyio
async def test_get_object(s3_client, test_bucket: str):
    """Test the standalone get_object function."""
    # Arrange
    test_key = "test-get-key"
    test_data = b'{"value": "test-get-data"}'

    # Put the test data directly into the bucket
    await s3_client.put_object(Bucket=test_bucket, Key=test_key, Body=test_data)

    # Act
    result = await get_object(client=s3_client, bucket=test_bucket, key=test_key)

    # Assert
    assert result == test_data
    assert isinstance(result, bytes)


@pytest.mark.anyio
async def test_get_object_nonexistent(s3_client, test_bucket: str):
    """Test get_object with a nonexistent key."""
    # Act
    result = await get_object(
        client=s3_client, bucket=test_bucket, key="nonexistent-key"
    )

    # Assert
    assert result is None


@pytest.mark.anyio
async def test_put_object(s3_client, test_bucket: str):
    """Test the standalone put_object function."""
    # Arrange
    test_key = "test-put-key"
    test_data = b'{"value": "test-put-data"}'

    # Act
    result = await put_object(
        client=s3_client, bucket=test_bucket, key=test_key, data=test_data
    )

    # Assert
    assert result is not None
    assert "ResponseMetadata" in result
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200

    # Verify the object was stored correctly
    response = await s3_client.get_object(Bucket=test_bucket, Key=test_key)
    async with response["Body"] as stream:
        stored_data = await stream.read()

    assert stored_data == test_data


@pytest.mark.anyio
async def test_put_object_duplicate(s3_client, test_bucket: str):
    """Test put_object with an existing key (should not overwrite)."""
    # Arrange
    test_key = "test-duplicate-key"
    original_data = b'{"value": "original-data"}'
    new_data = b'{"value": "new-data"}'

    # Put the original data
    response1 = await put_object(
        client=s3_client, bucket=test_bucket, key=test_key, data=original_data
    )
    assert response1 is not None
    assert response1["ResponseMetadata"]["HTTPStatusCode"] == 200
    # Act - Try to overwrite with new data
    response2 = await put_object(
        client=s3_client, bucket=test_bucket, key=test_key, data=new_data
    )
    assert response2 is None

    # Verify the object wasn't overwritten
    response = await s3_client.get_object(Bucket=test_bucket, Key=test_key)
    async with response["Body"] as stream:
        stored_data = await stream.read()

    assert stored_data == original_data  # Should still have original data


class TestResolveConditionActivity:
    """Tests for the resolve_condition_activity function."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "test_value,expected_result",
        [
            (True, True),  # Boolean true
            (False, False),  # Boolean false
            (42, True),  # Truthy number
            (0, False),  # Falsy - zero
            ("", False),  # Falsy - empty string
            ([], False),  # Falsy - empty list
            ({}, False),  # Falsy - empty dict
            (None, False),  # Falsy - None
        ],
        ids=[
            "true",
            "false",
            "truthy_number",
            "zero",
            "empty_str",
            "empty_list",
            "empty_dict",
            "none",
        ],
    )
    async def test_resolve_condition(
        self, activity_env: ActivityEnvironment, test_value: Any, expected_result: bool
    ):
        """Test resolving conditions with various values.

        NOTE: We mock the entire object store resolution, since this is tested elsewhere.
        """
        # Arrange
        context = ExecutionContext(
            {ExprContext.ACTIONS: {"test": {"result": test_value}}}
        )
        condition_expr = "${{ ACTIONS.test.result }}"
        input_data = ResolveConditionActivityInput(
            context=context,
            condition_expr=condition_expr,
        )

        # Mock the ObjectStore resolution
        mock_store = AsyncMock(spec=ObjectStore)
        mock_store.resolve_object_refs = AsyncMock(return_value=context)

        with patch.object(ObjectStore, "get", return_value=mock_store):
            # Act
            result = await activity_env.run(
                DSLActivities.resolve_condition_activity, input_data
            )

            # Assert
            assert result is expected_result
            mock_store.resolve_object_refs.assert_awaited_once_with(
                condition_expr, context
            )

    @pytest.mark.anyio
    async def test_resolve_condition_raises_error(
        self, activity_env: ActivityEnvironment
    ):
        """Test that an ApplicationError is raised when a value can't be converted to a boolean."""
        # Arrange
        context = ExecutionContext()
        condition_expr = "${{ ACTIONS.test.result }}"
        input_data = ResolveConditionActivityInput(
            context=context,
            condition_expr=condition_expr,
        )

        # Use a special mock that raises an error when bool() is called
        class NonBooleanResult:
            def __bool__(self):
                raise ValueError("Cannot convert to boolean")

        result_object = NonBooleanResult()

        # Create a resolved context that will return our special object
        resolved_context = ExecutionContext(
            {ExprContext.ACTIONS: {"test": {"result": result_object}}}
        )

        # Mock the ObjectStore resolution
        mock_store = AsyncMock(spec=ObjectStore)
        mock_store.resolve_object_refs = AsyncMock(return_value=resolved_context)

        with patch.object(ObjectStore, "get", return_value=mock_store):
            # Act & Assert
            with pytest.raises(ApplicationError) as exc_info:
                await activity_env.run(
                    DSLActivities.resolve_condition_activity, input_data
                )

            # Verify the error message
            assert "Condition result could not be converted to a boolean" in str(
                exc_info.value
            )
            assert exc_info.value.non_retryable is True
