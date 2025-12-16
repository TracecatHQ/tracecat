"""Fixtures for stress tests."""

from pathlib import Path

import pytest

from tracecat.dsl.common import DSLInput


@pytest.fixture
def dsl(request: pytest.FixtureRequest) -> DSLInput:
    """Load a DSL workflow from tests/data/workflows/{name}.yml"""
    test_name = request.param
    data_path = Path("tests/data/workflows") / f"{test_name}.yml"
    dsl = DSLInput.from_yaml(data_path)
    return dsl
