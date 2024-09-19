from pathlib import Path

import pytest
import yaml

from tracecat.registry import TemplateAction


def get_template_action_paths(dir_path: Path):
    paths = list(dir_path.rglob("*.yml"))
    print(paths)
    return paths


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_path",
    get_template_action_paths(Path(__file__).parents[3] / "templates"),
    ids=lambda x: x.stem,
)
async def test_template_action_validation(file_path):
    with open(file_path) as file:
        template = yaml.safe_load(file)
    action = TemplateAction(**template)
    assert action.type == "action"
    assert action.definition

    # TODO: Add LLM based validation checks
