from pathlib import Path

import pytest
import yaml

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
    with open(file_path) as file:
        template = yaml.safe_load(file)

    # Test parsing
    action = TemplateAction(**template)
    assert action.type == "action"
    assert action.definition

    # Test registration
    registry.register_template_action(action)
    assert action.definition.action in registry
