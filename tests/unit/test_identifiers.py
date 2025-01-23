import uuid
from typing import Self
from uuid import UUID

import pytest
from pydantic import BaseModel

from tracecat.identifiers.common import (
    TracecatUUID,
    id_from_short,
    id_to_short,
)
from tracecat.identifiers.workflow import WorkflowUUID


def test_id_to_short() -> None:
    """Test conversion of UUID to shortened string format."""
    test_uuid = UUID(int=12345)
    result = id_to_short(test_uuid, prefix="test_")

    # Verify prefix
    assert result.startswith("test_")
    # Verify total length (prefix + 22 chars for padded base62)
    assert len(result) == len("test_") + 22
    # Verify we can convert it back
    assert id_from_short(result, "test_") == test_uuid


def test_id_from_short_invalid_prefix() -> None:
    """Test that id_from_short raises ValueError for invalid prefix."""
    with pytest.raises(ValueError, match="Invalid short ID string"):
        id_from_short("invalid_12345", prefix="test_")


class MockUUID(TracecatUUID[str]):
    """Test implementation of TracecatUUID."""

    prefix = "test_"
    legacy_prefix = "test-legacy-"

    @classmethod
    def from_legacy(cls, id: str) -> Self:
        assert cls.legacy_prefix is not None
        assert id.startswith(cls.legacy_prefix)
        n = len(cls.legacy_prefix)
        return cls.from_uuid(UUID(id[n:]))


@pytest.fixture
def test_uuid_str() -> str:
    """Fixture providing a consistent test UUID string."""
    return "12345678-1234-1234-1234-123456789012"


class TestTracecatUUID:
    """Test suite for TracecatUUID base class."""

    def test_missing_prefix(self) -> None:
        """Test that TracecatUUID requires a prefix."""

        class InvalidUUID(TracecatUUID):
            pass

        with pytest.raises(ValueError, match="requires a prefix to be set"):
            InvalidUUID()

    def test_short_conversion(self) -> None:
        """Test conversion to and from short format."""
        test_uuid = UUID(int=12345)
        uuid_obj = MockUUID(int=test_uuid.int)
        short_id = uuid_obj.short()

        # Test conversion to short format
        assert short_id.startswith("test_")
        assert len(short_id) == len("test_") + 22

        # Test conversion back from short format
        recovered = MockUUID.from_short(short_id)
        assert recovered == uuid_obj

    def test_new_uuid4(self) -> None:
        """Test generation of new UUID4."""
        uuid1 = MockUUID.new_uuid4()
        uuid2 = MockUUID.new_uuid4()

        assert isinstance(uuid1, MockUUID)
        assert uuid1 != uuid2  # Verify uniqueness

    def test_make_short(self) -> None:
        """Test make_short class method."""

        original_uuid = UUID(int=12345)
        short_id = MockUUID.make_short(original_uuid)

        assert short_id.startswith("test_")
        assert len(short_id) == len("test_") + 22

    def test_from_any_invalid(self) -> None:
        """Test new with invalid input."""

        with pytest.raises(ValueError, match="Invalid MockUUID ID:"):
            MockUUID.new(123)  # type: ignore

        with pytest.raises(ValueError):
            MockUUID.new("not-a-uuid")

    def test_equality(self) -> None:
        """Test that TracecatUUID instances can be compared using ==."""

        # Create two UUIDs with the same value
        uuid_int = 12345
        uuid1 = MockUUID(int=uuid_int)
        uuid2 = MockUUID(int=uuid_int)

        # Create a different UUID for comparison
        uuid3 = MockUUID(int=67890)

        # Test equality
        assert uuid1 == uuid2
        assert uuid1 != uuid3

        # Test equality with regular UUID
        regular_uuid = UUID(int=uuid_int)
        assert uuid1 == regular_uuid

        # Test equality with non-UUID type
        assert uuid1 != "not-a-uuid"
        assert uuid1 != 12345

    def test_equality_with_normal_uuid(self) -> None:
        """Test that TracecatUUID instances can be compared with normal UUIDs."""

        test_uuid = MockUUID(int=12345)
        normal_uuid = UUID(int=12345)
        assert test_uuid == normal_uuid


class TestWorkflowUUID:
    """Test suite for WorkflowUUID."""

    def test_prefix(self) -> None:
        """Test WorkflowUUID prefix."""
        assert WorkflowUUID.prefix == "wf_"

    def test_roundtrip(self) -> None:
        """Test conversion to and from short format."""
        original = WorkflowUUID.new_uuid4()
        short_id = original.short()
        recovered = WorkflowUUID.from_short(short_id)
        assert original == recovered

    def test_from_uuid(self) -> None:
        """Test creation from regular UUID."""
        regular_uuid = UUID(int=12345)
        workflow_uuid = WorkflowUUID.from_uuid(regular_uuid)
        assert workflow_uuid.int == regular_uuid.int

    def test_from_legacy_valid(self) -> None:
        """Test from_legacy with valid legacy ID."""
        # Legacy IDs are 'wf' followed by UUID hex without hyphens
        id = uuid.uuid4()
        legacy_id = "wf-" + id.hex
        workflow_uuid = WorkflowUUID.from_legacy(legacy_id)
        assert isinstance(workflow_uuid, WorkflowUUID)
        assert workflow_uuid == id

    def test_from_legacy_invalid(self) -> None:
        """Test from_legacy with invalid legacy ID."""
        invalid_ids = [
            "workflow_123",  # Wrong prefix
            "wf_short",  # Too short
            "wfGHIJKLMN",  # Invalid hex
        ]

        for invalid_id in invalid_ids:
            with pytest.raises(ValueError):
                WorkflowUUID.from_legacy(invalid_id)


def test_tracecat_uuid_serialization(test_uuid_str):
    """Test that TracecatUUID can be serialized and deserialized correctly."""

    class TestModel(BaseModel):
        """Test model that uses TestUUID."""

        id: MockUUID

    original_uuid = UUID(test_uuid_str)
    test_uuid = MockUUID.from_uuid(original_uuid)

    assert MockUUID.new(test_uuid_str) == test_uuid
    assert MockUUID.new(test_uuid) == test_uuid
    assert MockUUID.new(test_uuid.short()) == test_uuid

    # Create a model instance
    model = TestModel(id=test_uuid)

    # Serialize to dict/JSON
    serialized_json = model.model_dump_json()
    assert serialized_json == f'{{"id":"{test_uuid}"}}'

    serialized_python = model.model_dump()
    assert serialized_python == {"id": test_uuid}

    # Try from uuid string

    # Deserialize back
    deserialized = TestModel.model_validate_json(serialized_json)

    # Verify the UUID matches
    assert isinstance(deserialized.id, MockUUID)
    assert deserialized.id == test_uuid
    assert str(deserialized.id) == str(original_uuid)

    # Test direct string UUID initialization
    str_model = TestModel(id=str(original_uuid))  # type: ignore
    assert str_model.id == test_uuid


def test_tracecat_uuid_construction(test_uuid_str):
    """Test that TracecatUUID can be constructed from various inputs."""
    original_uuid = UUID(test_uuid_str)
    test_uuid = MockUUID.from_uuid(original_uuid)
    short_id = test_uuid.short()

    # Test model construction
    class TestModel(BaseModel):
        """Test model that uses MockUUID."""

        id: MockUUID

    from_uuid_str = TestModel(id=test_uuid_str)  # type: ignore
    assert isinstance(from_uuid_str.id, MockUUID)
    assert from_uuid_str.id == test_uuid

    json_str = f'{{"id":"{test_uuid_str}"}}'
    from_json_str = TestModel.model_validate_json(json_str)
    assert isinstance(from_json_str.id, MockUUID)
    assert from_json_str.id == MockUUID(test_uuid_str)

    json_dict = {"id": test_uuid}
    from_json_dict = TestModel.model_validate(json_dict)
    assert isinstance(from_json_dict.id, MockUUID)
    assert from_json_dict.id == test_uuid

    # short id
    assert MockUUID.from_short(short_id) == test_uuid

    # We actually get a type error here, but it works anyway
    from_short_str = TestModel(id=short_id)  # type: ignore
    assert isinstance(from_short_str.id, MockUUID)
    assert from_short_str.id == test_uuid

    with pytest.raises(
        ValueError, match="Invalid prefix 'test_' for MockUUID, expected 'test_'"
    ):
        MockUUID.from_short("invalid_12345")

    # Legacy

    assert MockUUID.from_legacy(f"test-legacy-{test_uuid.hex}") == test_uuid
    assert MockUUID.new(f"test-legacy-{test_uuid.hex}") == test_uuid
