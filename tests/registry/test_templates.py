from pathlib import Path

import pytest

from tracecat.registry.actions.models import TemplateAction
from tracecat.registry.repository import Repository


@pytest.fixture(scope="module")
def registry():
    registry = Repository()
    registry.init(include_base=False, include_templates=True)
    return registry


@pytest.mark.anyio
@pytest.mark.parametrize(
    "file_path",
    list(Path("registry/tracecat_registry/templates").rglob("*.yml")),
    ids=lambda path: str(path.parts[-2:]),
)
async def test_template_action_validation(file_path, registry):
    # Test parsing
    action = TemplateAction.from_yaml(file_path)
    assert action.type == "action"
    assert action.definition

    # Test registration
    registry.register_template_action(action)
    assert action.definition.action in registry
