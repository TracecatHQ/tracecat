from __future__ import annotations

import pytest
from tracecat_ee.agent.workflows.durable import _remaining_limit


@pytest.mark.parametrize(
    ("limit", "consumed", "expected"),
    [
        (None, 5, None),
        (10, None, 10),
        (10, 3, 7),
        (10, 10, 0),
        (10, 12, 0),
    ],
)
def test_remaining_limit(
    limit: int | None,
    consumed: int | None,
    expected: int | None,
) -> None:
    assert _remaining_limit(limit, consumed) == expected
