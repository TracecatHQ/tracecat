"""Test LLM Action Templates."""

from pathlib import Path

import pytest

from tests.shared import glob_file_paths, load_yaml_template_action
from tracecat.executor import service
from tracecat.registry.actions.models import TemplateAction
from tracecat.registry.repository import Repository

LLM_TEMPLATES_DIR = Path("registry/tracecat_registry/templates/llm")


@pytest.fixture(scope="module")
def llm_actions_repo():
    repo = Repository()
    repo.init(include_base=True, include_templates=True)
    yield repo


@pytest.fixture(
    scope="function",
    params=glob_file_paths(LLM_TEMPLATES_DIR / "extract_one", "yml"),
)
def extract_one(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=glob_file_paths(LLM_TEMPLATES_DIR / "extract_many", "yml"),
)
def extract_many(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=glob_file_paths(LLM_TEMPLATES_DIR / "summarize", "yml"),
)
def summarize(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.mark.anyio
async def test_extract_one(extract_one: TemplateAction, llm_actions_repo: Repository):
    llm_actions_repo.register_template_action(extract_one)
    bound_action = llm_actions_repo.get(extract_one.definition.action)

    result = await service.run_template_action(
        action=bound_action,
        args={
            "input": "San Francisco is home to many tech startups and the famous Golden Gate Bridge.",
            "input_context": "city information",
            "output_name": "landmark",
            "output_type": "string",
            "output_context": "A famous landmark in San Francisco",
        },
        context={},
    )

    assert result is not None
    assert isinstance(result, str)
    assert "golden gate" in result.lower()


@pytest.mark.anyio
async def test_extract_many(extract_many: TemplateAction, llm_actions_repo: Repository):
    llm_actions_repo.register_template_action(extract_many)
    bound_action = llm_actions_repo.get(extract_many.definition.action)

    input_text = """
    Y Combinator has funded many successful startups from San Francisco including:

    Airbnb (2009) - Transformed the hospitality industry
    Stripe (2010) - Revolutionized online payments
    Dropbox (2007) - Changed how we store and share files
    DoorDash (2013) - Disrupted food delivery services
    """

    result = await service.run_template_action(
        action=bound_action,
        args={
            "input": input_text,
            "input_context": "startup information",
            "output_name": "companies",
            "output_type": "string",
            "output_context": "Successful Y Combinator startups from San Francisco",
        },
        context={},
    )

    assert result is not None
    assert isinstance(result, dict)
    assert "companies" in result
    assert isinstance(result["companies"], list)

    companies_lower = [company.lower() for company in result["companies"]]
    assert any(
        company in companies_lower
        for company in ["airbnb", "stripe", "dropbox", "doordash"]
    )
    assert len(result["companies"]) >= 2, "Should extract at least two YC companies"


@pytest.mark.anyio
async def test_summarize(summarize: TemplateAction, llm_actions_repo: Repository):
    llm_actions_repo.register_template_action(summarize)
    bound_action = llm_actions_repo.get(summarize.definition.action)

    input_text = """
    San Francisco, officially the City and County of San Francisco, is a cultural, commercial, and financial
    center in the U.S. state of California. Located in Northern California, San Francisco is the 17th most
    populous city in the United States, and the fourth most populous in California, with 808,437 residents as of 2022.

    San Francisco is known for its startup ecosystem and is home to Y Combinator, one of the world's most
    successful startup accelerators. Founded in 2005, Y Combinator has funded over 3,000 companies with a
    combined valuation exceeding $300 billion. The "YC effect" is well-known in the tech industry - when a
    startup gets accepted into Y Combinator, their chances of success increase dramatically. The accelerator's
    motto "Make Something People Want" has become a guiding principle for entrepreneurs worldwide.

    The city's fog, steep rolling hills, eclectic mix of architecture, and landmarks, including the Golden Gate
    Bridge, cable cars, Alcatraz, and Chinatown, make it one of the most visited cities in the world.
    """

    result = await service.run_template_action(
        action=bound_action,
        args={"input": input_text, "input_context": "tech startup ecosystems"},
        context={},
    )

    assert result is not None
    assert isinstance(result, str)
    assert any(
        term in result.lower() for term in ["san francisco", "y combinator", "startup"]
    )
