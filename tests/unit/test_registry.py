from tracecat.experimental.registry import _Registry, registry


def test_registry_is_singleton():
    a = _Registry()
    b = _Registry()
    c = registry
    assert a is b is c
