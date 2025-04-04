"""Tests for the store models."""

from tracecat.ee.store.models import ObjectRef, as_object_ref


class TestObjectRef:
    """Tests for the ObjectRef model and related functions."""

    def test_as_object_ref_valid_data(self):
        """Test as_object_ref with valid ObjectRef data."""
        # Arrange
        valid_data = {
            "metadata": {"encoding": "json/plain"},
            "size": 100,
            "digest": "test-digest",
            "key": "blobs/default/test-digest",
        }

        # Act
        result = as_object_ref(valid_data)

        # Assert
        assert result is not None
        assert isinstance(result, ObjectRef)
        assert result.metadata == {"encoding": "json/plain"}
        assert result.size == 100
        assert result.digest == "test-digest"
        assert result.key == "blobs/default/test-digest"

    def test_as_object_ref_missing_required_fields(self):
        """Test as_object_ref with data missing required fields."""
        # Arrange - missing size
        missing_size = {
            "metadata": {"encoding": "json/plain"},
            "digest": "test-digest",
            "key": "blobs/default/test-digest",
        }

        # Act & Assert
        assert as_object_ref(missing_size) is None

        # Arrange - missing digest
        missing_digest = {
            "metadata": {"encoding": "json/plain"},
            "size": 100,
            "key": "blobs/default/test-digest",
        }

        # Act & Assert
        assert as_object_ref(missing_digest) is None

        # Arrange - missing key
        missing_key = {
            "metadata": {"encoding": "json/plain"},
            "size": 100,
            "digest": "test-digest",
        }

        # Act & Assert
        assert as_object_ref(missing_key) is None

    def test_as_object_ref_with_none(self):
        """Test as_object_ref with None."""
        # Act & Assert
        assert as_object_ref(None) is None

    def test_as_object_ref_with_invalid_type(self):
        """Test as_object_ref with an invalid type."""
        # Act & Assert
        assert as_object_ref("not a dict") is None
        assert as_object_ref(123) is None
        assert as_object_ref([]) is None

    def test_as_object_ref_extra_fields(self):
        """Test as_object_ref with extra fields."""
        # Arrange
        data_with_extra = {
            "metadata": {"encoding": "json/plain"},
            "size": 100,
            "digest": "test-digest",
            "key": "blobs/default/test-digest",
            "extra_field": "should be ignored",
        }

        # Act
        result = as_object_ref(data_with_extra)

        # Assert
        assert result is not None
        assert isinstance(result, ObjectRef)
        assert result.metadata == {"encoding": "json/plain"}
        assert result.size == 100
        assert result.digest == "test-digest"
        assert result.key == "blobs/default/test-digest"
        # Extra field should not be included in the model
        assert not hasattr(result, "extra_field")

    def test_as_object_ref_with_invalid_field_types(self):
        """Test as_object_ref with fields of incorrect types."""
        # Note: Pydantic automatically converts strings to integers when possible
        # So "100" is converted to 100 automatically and doesn't fail validation

        # Arrange - something that can't be converted to an integer
        invalid_size_type = {
            "metadata": {"encoding": "json/plain"},
            "size": "not-a-number",  # String that can't be converted to int
            "digest": "test-digest",
            "key": "blobs/default/test-digest",
        }

        # Act & Assert
        assert as_object_ref(invalid_size_type) is None

        # Arrange - invalid nested field type
        invalid_metadata_type = {
            "metadata": 123,  # Should be a dictionary
            "size": 100,
            "digest": "test-digest",
            "key": "blobs/default/test-digest",
        }

        # Act & Assert
        assert as_object_ref(invalid_metadata_type) is None
