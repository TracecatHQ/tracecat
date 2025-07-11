import pytest
from tracecat_registry import SecretNotFoundError, secrets


@pytest.mark.anyio
async def test_secrets_get_raises_on_missing():
    """Test that secrets.get raises SecretNotFoundError when secret is not found."""
    with pytest.raises(SecretNotFoundError) as exc_info:
        secrets.get("NONEXISTENT_SECRET")

    assert "Secret 'NONEXISTENT_SECRET' is required but not found." in str(
        exc_info.value
    )


@pytest.mark.anyio
async def test_secrets_get_returns_value_when_set():
    """Test that secrets.get returns the value when secret is set."""
    # Set a test secret
    secrets.set("TEST_SECRET", "test_value")

    # Get should return the value
    result = secrets.get("TEST_SECRET")
    assert result == "test_value"


@pytest.mark.anyio
async def test_secrets_get_or_default_returns_none_when_missing():
    """Test that secrets.get_or_default returns None when secret is not found and no default provided."""
    result = secrets.get_or_default("NONEXISTENT_SECRET")
    assert result is None


@pytest.mark.anyio
async def test_secrets_get_or_default_returns_default_when_missing():
    """Test that secrets.get_or_default returns default value when secret is not found."""
    result = secrets.get_or_default("NONEXISTENT_SECRET", "default_value")
    assert result == "default_value"


@pytest.mark.anyio
async def test_secrets_get_or_default_returns_value_when_set():
    """Test that secrets.get_or_default returns the actual value when secret is set."""
    # Set a test secret
    secrets.set("TEST_SECRET_WITH_DEFAULT", "actual_value")

    # Should return actual value, not default
    result = secrets.get_or_default("TEST_SECRET_WITH_DEFAULT", "default_value")
    assert result == "actual_value"

    # Should return actual value when no default provided
    result = secrets.get_or_default("TEST_SECRET_WITH_DEFAULT")
    assert result == "actual_value"


@pytest.mark.anyio
async def test_secrets_set_and_get_workflow():
    """Test the complete workflow of setting and getting secrets."""
    # Initially should not exist
    with pytest.raises(SecretNotFoundError):
        secrets.get("WORKFLOW_SECRET")

    # Set the secret
    secrets.set("WORKFLOW_SECRET", "workflow_value")

    # Now get should work
    assert secrets.get("WORKFLOW_SECRET") == "workflow_value"

    # get_or_default should also return the actual value
    assert (
        secrets.get_or_default("WORKFLOW_SECRET", "ignored_default") == "workflow_value"
    )
