from pathlib import Path

import pytest
import yaml

from tracecat.registry.actions.models import TemplateAction


@pytest.mark.anyio
@pytest.mark.parametrize(
    "file_path",
    list(Path("registry/tracecat_registry/templates").rglob("*.yml")),
    ids=lambda path: str(path.parts[-2:]),
)
async def test_template_action_validation(file_path):
    with open(file_path) as file:
        template = yaml.safe_load(file)
    action = TemplateAction(**template)
    assert action.type == "action"
    assert action.definition
