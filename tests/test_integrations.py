import pytest

from tracecat.integrations._registry import Registry, registry


@pytest.fixture(autouse=True)
def setup_tests():
    # Clear the registry before each test
    registry._integrations = {}
    registry._metadata = {}
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
    def add(nums: list[int]) -> int:
        """Adds integers together."""
        return sum(nums)

    input_data = [1, 2]
    # Key format is "integrations.<module_name>.<function_name>"
    # The module name would be 'test_integrations' (current file) in this case
    expected_key = "integrations.test_integrations.add"
    assert add(input_data) == 3
    assert expected_key in registry.integrations
    assert registry.integrations[expected_key](input_data) == 3
    assert registry.metadata[expected_key]["description"] == "Test description"
