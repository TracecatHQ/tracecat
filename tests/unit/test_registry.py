import textwrap

import pytest

from tracecat import config
from tracecat.registry import RegistryValidationError, _Registry, registry


def blank_registry_is_singleton():
    a = _Registry()
    b = _Registry()
    c = registry
    assert a is b is c


def test_udf_validate_args():
    """This tests the UDF.validate_args method, which shouldn't raise any exceptions
    when given a templated expression.
    """
    import sys
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    try:
        # Create a new module
        test_module = ModuleType("test_module")
        # Create a module spec for the test module
        module_spec = ModuleSpec("test_module", None)
        test_module.__spec__ = module_spec
        sys.modules["test_module"] = test_module

        # Define the function in the new module
        exec(
            textwrap.dedent(
                """
                from tracecat.registry import registry

                @registry.register(
                    description="This is a test function",
                    namespace="test",
                )
                def test_function(num: int) -> int:
                    return num
                """
            ),
            test_module.__dict__,
        )

        # Import the function from the new module

        registry._register_udfs_from_module(test_module, visited_modules=set())
        udf = registry.get("test.test_function")
        udf.validate_args(num="${{ path.to.number }}")
        udf.validate_args(num=1)
        with pytest.raises(RegistryValidationError):
            udf.validate_args(num="not a number")
    finally:
        # Clean up
        del sys.modules["test_module"]


def test_load_base_udfs():
    registry._reset()
    assert len(registry) == 0
    registry.init(include_base=True, include_templates=False, include_remote=False)
    assert len(registry) > 0


@pytest.mark.webtest
def test_load_remote_udfs(blank_registry: _Registry, env_sandbox):
    from tracecat.logger import logger

    logger.info("Remote url", url=config.TRACECAT__REMOTE_REGISTRY_URL)
    keys_before = set(blank_registry.keys.copy())
    blank_registry._remote = config.TRACECAT__REMOTE_REGISTRY_URL
    blank_registry.init(
        include_base=False, include_templates=False, include_remote=True
    )
    keys_after = set(registry.keys.copy())
    diff = keys_after - keys_before
    assert diff == {
        "integrations.greetings.say_hello_world",
        "integrations.greetings.say_goodbye",
    }


def blank_registry_function_can_be_called():
    """We need to test that the ordering of the workflow tasks is correct."""
    import sys
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    registry._reset()
    assert len(registry) == 0

    try:
        # Create a new module
        test_module = ModuleType("test_module")
        # Create a module spec for the test module
        module_spec = ModuleSpec("test_module", None)
        test_module.__spec__ = module_spec
        sys.modules["test_module"] = test_module

        # Define the function in the new module
        exec(
            textwrap.dedent(
                """
                from tracecat.registry import registry

                @registry.register(
                    description="This is a test function",
                    namespace="test",
                )
                def test_function(num: int) -> int:
                    return num
                """
            ),
            test_module.__dict__,
        )

        # Import the function from the new module

        registry._register_udfs_from_module(test_module, visited_modules=set())
        udf = registry.get("test.test_function")
        for i in range(10):
            assert udf.fn(num=i) == i
    finally:
        # Clean up
        del sys.modules["test_module"]


@pytest.mark.asyncio
async def blank_registry_async_function_can_be_called():
    import sys
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    registry._reset()
    assert len(registry) == 0

    try:
        # Create a new module
        test_module = ModuleType("test_module")
        # Create a module spec for the test module
        module_spec = ModuleSpec("test_module", None)
        test_module.__spec__ = module_spec
        sys.modules["test_module"] = test_module

        # Define the function in the new module
        exec(
            textwrap.dedent(
                """
                from tracecat.registry import registry

                @registry.register(
                    description="This is a test function",
                    namespace="test",
                )
                async def test_function(num: int) -> int:
                    return num
                """
            ),
            test_module.__dict__,
        )

        # Import the function from the new module

        registry._register_udfs_from_module(test_module, visited_modules=set())
        udf = registry.get("test.test_function")
        for i in range(10):
            assert await udf.fn(num=i) == i
    finally:
        # Clean up
        del sys.modules["test_module"]
