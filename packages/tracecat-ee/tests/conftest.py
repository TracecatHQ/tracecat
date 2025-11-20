import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Pin anyio to asyncio backend so Trio is not required."""
    return "asyncio"
