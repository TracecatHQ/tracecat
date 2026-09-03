import pytest
from tracecat_registry.core import ai


@pytest.mark.parametrize(
    "action",
    ["rank_documents", "select_field", "select_fields"],
)
def test_removed_ai_actions_are_not_exported(action: str) -> None:
    assert not hasattr(ai, action)
