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


async def test_naming_conventions():
    """The following naming conventions are followed across UDFs and template actions.

    - Fields: `namespace, doc_url, default_title, description must be present.
    - `namespace` should be a valid Python package name.
    - `doc_url` should be a valid URL.
    - Only first letter of the first word in the default title should be capitalized.
    - The default title should be short and match the tool's ontology and API naming conventions.
    - The tool name (e.g. Elastic) should not be in the default title.
    - The description should be maximum two sentences, ending with a period, and match the tool's ontology and API naming conventions.
    - If the vendor has multiple products, the namespace should be `<vendor_name>_<product_name>` e.g. `elastic_security`, `elastic_search`.
    """
    pass


async def test_template_action_schema_normalization():
    """All template actions

    - Secrets should only contain SENSITIVE data. Things like domain names, scopes should be visible in the UI.
    """
    pass
