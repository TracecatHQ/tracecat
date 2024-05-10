import pytest

from tracecat.auth import Role
from tracecat.contexts import ctx_session_role
from tracecat.integrations._registry import Registry, registry
from tracecat.runner.actions import run_integration_action


@pytest.fixture(autouse=True)
def setup_tests():
    # Clear the registry before each test
    registry._integrations = {}
    registry._metadata = {}

    # Role
    service_role = Role(
        type="service", user_id="mock_user_id", service_id="mock_service_id"
    )
    ctx_session_role.set(service_role)
    yield


def test_is_singleton():
    a = Registry()
    b = Registry()
    c = registry
    assert a is b is c


def test_simple_integration():
    # Import the integration

    @registry.register(
        description="Test description",
    )
    def add1(nums: list[int]) -> int:
        """Adds integers together."""
        return sum(nums)

    input_data = [1, 2]
    # Key format is "integrations.<module_name>.<function_name>"
    # The module name would be 'test_integrations' (current file) in this case
    expected_qualifier = "integrations.test_integrations.add1"
    assert add1(input_data) == 3
    assert expected_qualifier in registry.integrations
    assert registry.integrations[expected_qualifier](input_data) == 3
    assert registry.metadata[expected_qualifier]["description"] == "Test description"


@pytest.mark.asyncio
async def test_run_integration_action_no_secrets():
    """Test running an integration action."""
    # Role has already been set in the fixture
    # Register the integration

    @registry.register(
        description="Test description",
    )
    def add2(nums: list[int]) -> int:
        """Adds integers together."""
        return sum(nums)

    input_data = [1, 2]
    # Key format is "integrations.<module_name>.<function_name>"
    # The module name would be 'test_integrations' (current file) in this case
    expected_qualifier = "integrations.test_integrations.add2"
    expected = 3

    # Rune the integration action
    actual_output = await run_integration_action(
        qualname=expected_qualifier,
        params={"nums": input_data},
    )
    actual = actual_output["output"]
    assert actual == expected
