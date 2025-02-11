from tracecat_registry import RegistrySecret, registry, secrets


@registry.register(
    description="This is a test function",
    namespace="testing",
)
def add_100(num: int) -> int:
    return num + 100


@registry.register(
    description="This is a test function that adds a list of numbers",
    namespace="testing",
)
def add_nums(nums: list[int]) -> int:
    return sum(nums)


test_secret = RegistrySecret(name="test", keys=["KEY"])


@registry.register(
    description="UDF that uses secrets",
    namespace="testing",
    secrets=[test_secret],
)
def fetch_secret(secret_key_name: str) -> str | None:
    secret = secrets.get(secret_key_name)
    return secret
