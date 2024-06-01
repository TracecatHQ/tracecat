import pytest

from tracecat.registry import RegistryValidationError, _Registry, registry


def test_registry_is_singleton():
    a = _Registry()
    b = _Registry()
    c = registry
    assert a is b is c


def test_udf_validate_args():
    """This tests the UDF.validate_args method, which shouldn't raise any exceptions
    when given a templated expression.
    """

    @registry.register(
        description="This is a test function",
        namespace="test",
    )
    def test_function(num: int) -> int:
        return num

    registry.init()
    udf = registry.get("test.test_function")
    udf.validate_args(num="${{ path.to.number }}")
    udf.validate_args(num=1)
    with pytest.raises(RegistryValidationError):
        udf.validate_args(num="not a number")
