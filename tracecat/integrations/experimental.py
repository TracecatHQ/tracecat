import time


def experimental_integration(a: int, b: int) -> int:
    """This is the docstring of an experimental integration"""
    print(f"Running experimental integration with a={a} and b={b}")
    time.sleep(2)
    return a + b


experimental_integration.__integration_metadata__ = {
    "description": "This is the description that will be shown in the library"
}


def experimental_integration_v2(a: int, b: int, c: int) -> int:
    """This is the docstring of an experimental integration v2"""
    print(f"Running experimental integration v2 with a={a} and b={b}")
    time.sleep(2)
    return a + b + c


experimental_integration_v2.__integration_metadata__ = {
    "description": "This is the description for the that will be shown in the library"
}
