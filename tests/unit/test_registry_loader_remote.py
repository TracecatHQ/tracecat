"""Test the improved registry loader functions."""

import pytest
from unittest.mock import MagicMock, patch
from pydantic_core import PydanticUndefined

from tracecat.db.schemas import RegistryAction
from tracecat.registry.loaders import (
    _load_action_from_database,
    load_udf_impl,
    get_bound_action_impl,
)
from tracecat.registry.actions.models import RegistryActionUDFImpl
from tracecat.types.exceptions import RegistryError


@pytest.mark.anyio
async def test_load_action_from_database_success():
    """Test successful database loading with valid schema."""
    mock_action = MagicMock(spec=RegistryAction)
    mock_action.name = "test_action"
    mock_action.interface = {
        "expects": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "recipient": {"type": "string"},
                "optional_field": {"type": "string"},
            },
            "required": ["message", "recipient"],
        }
    }

    result = _load_action_from_database(mock_action, "test_function")

    assert result is not None
    args_cls, rtype_adapter = result
    assert rtype_adapter is None

    # Test that the generated model has correct fields
    fields = args_cls.model_fields
    assert "message" in fields
    assert "recipient" in fields
    assert "optional_field" in fields

    # Test that required fields have PydanticUndefined default
    assert fields["message"].default is PydanticUndefined
    assert fields["recipient"].default is PydanticUndefined
    assert fields["optional_field"].default is None


@pytest.mark.anyio
async def test_load_action_from_database_invalid_schema():
    """Test database loading with malformed schema."""
    mock_action = MagicMock(spec=RegistryAction)
    mock_action.name = "test_action"
    # Malformed schema - properties is not a dict
    mock_action.interface = {
        "expects": {
            "properties": "invalid",  # Should be dict
            "required": ["message"],
        }
    }

    result = _load_action_from_database(mock_action, "test_function")
    assert result is None  # Should gracefully fail


@pytest.mark.anyio
async def test_load_action_from_database_missing_interface():
    """Test database loading with missing interface."""
    mock_action = MagicMock(spec=RegistryAction)
    mock_action.interface = {}

    result = _load_action_from_database(mock_action, "test_function")
    assert result is None


@pytest.mark.anyio
async def test_load_udf_impl_remote_validation_mode():
    """Test load_udf_impl with remote module in validation mode."""
    impl = RegistryActionUDFImpl(
        type="udf",
        url="git+ssh://git@github.com/user/repo",
        module="custom_registry.actions",
        name="test_function"
    )

    mock_action = MagicMock(spec=RegistryAction)
    mock_action.name = "test_action"
    mock_action.interface = {
        "expects": {
            "type": "object",
            "properties": {"param": {"type": "string"}},
            "required": ["param"]
        }
    }

    with patch('importlib.import_module', side_effect=ModuleNotFoundError("Module not found")):
        result = load_udf_impl(impl, mock_action, mode="validation")

        # Should return database fallback
        assert isinstance(result, tuple)
        args_cls, rtype_adapter = result
        assert rtype_adapter is None
        assert "param" in args_cls.model_fields


@pytest.mark.anyio
async def test_load_udf_impl_remote_execution_mode():
    """Test load_udf_impl with remote module in execution mode."""
    impl = RegistryActionUDFImpl(
        type="udf",
        url="git+ssh://git@github.com/user/repo",
        module="custom_registry.actions",
        name="test_function"
    )

    mock_action = MagicMock(spec=RegistryAction)

    with patch('importlib.import_module', side_effect=ModuleNotFoundError("Module not found")):
        with pytest.raises(RegistryError, match="Required module.*not found"):
            load_udf_impl(impl, mock_action, mode="execution")



@pytest.mark.anyio
async def test_get_bound_action_impl_database_fallback():
    """Test full get_bound_action_impl flow with database fallback."""
    mock_action = MagicMock(spec=RegistryAction)
    mock_action.name = "test_action"
    mock_action.namespace = "custom"
    mock_action.description = "Test action"
    mock_action.secrets = []
    mock_action.implementation = {
        "type": "udf",
        "url": "git+ssh://git@github.com/user/repo",
        "module": "custom_registry.actions",
        "name": "test_function"
    }
    mock_action.interface = {
        "expects": {
            "type": "object",
            "properties": {"param": {"type": "string"}},
            "required": ["param"]
        }
    }
    mock_action.default_title = None
    mock_action.display_group = None
    mock_action.doc_url = None
    mock_action.author = None
    mock_action.deprecated = None
    mock_action.origin = "test"

    with patch('importlib.import_module', side_effect=ModuleNotFoundError("Module not found")):
        bound_action = get_bound_action_impl(mock_action, mode="validation")

        # Should have database-generated args_cls
        assert bound_action.args_cls is not None
        assert "param" in bound_action.args_cls.model_fields
        assert bound_action.args_cls.model_fields["param"].default is PydanticUndefined

        # Should have placeholder function
        with pytest.raises(NotImplementedError, match="Module.*not available"):
            bound_action.fn()