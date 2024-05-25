import pytest

from tracecat.auth import Role
from tracecat.contexts import ctx_role
from tracecat.experimental.registry import _Registry, registry


@pytest.fixture(autouse=True)
def setup_tests():
    # Clear the registry before each test
    ...
    # Role
    service_role = Role(
        type="service", user_id="mock_user_id", service_id="mock_service_id"
    )
    ctx_role.set(service_role)
    yield


def test_registry_is_singleton():
    a = _Registry()
    b = _Registry()
    c = registry
    assert a is b is c
