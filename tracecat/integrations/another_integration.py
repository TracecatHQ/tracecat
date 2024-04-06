import time


def integration_1(a: int, b: int) -> int:
    """This is the docstring of an experimental integration"""
    print(f"Running experimental integration with a={a} and b={b}")
    time.sleep(2)
    return a + b


integration_1.__integration_metadata__ = {
    "description": "This is the description that will be shown in the library",
}
