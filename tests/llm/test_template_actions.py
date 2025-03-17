"""Test LLM Action Templates."""

from pathlib import Path

import pytest

from tests.shared import glob_file_paths, load_yaml_template_action
from tracecat.registry.repository import Repository

LLM_TEMPLATES_DIR = Path("registry/tracecat_registry/templates/llm")


@pytest.fixture(scope="module")
def llm_actions_registry():
    repo = Repository()
    repo.init(include_base=True, include_templates=True)
    yield repo


@pytest.fixture(
    scope="function",
    params=glob_file_paths(LLM_TEMPLATES_DIR / "extract_one", "yaml"),
)
def extract_one(request: pytest.FixtureRequest):
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=glob_file_paths(LLM_TEMPLATES_DIR / "extract_many", "yaml"),
)
def extract_many(request: pytest.FixtureRequest):
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=glob_file_paths(LLM_TEMPLATES_DIR / "summarize", "yaml"),
)
def summarize(request: pytest.FixtureRequest):
    return load_yaml_template_action(request.param)
