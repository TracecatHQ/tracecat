from tracecat_registry import registry


@registry.register(
    namespace="fixture.arithmetic",
    description="Add 300 to an integer in the registry integration test.",
)
def add_300(number: int) -> int:
    return number + 300
