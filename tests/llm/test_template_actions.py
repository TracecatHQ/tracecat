"""Test LLM Action Templates."""

import os
from pathlib import Path

import orjson
import pytest

from tests.shared import load_yaml_template_action
from tracecat.executor import service
from tracecat.llm import async_openai_call
from tracecat.logger import logger
from tracecat.registry.actions.models import TemplateAction
from tracecat.registry.repository import Repository

LLM_TEMPLATES_DIR = Path("registry/tracecat_registry/templates/llm")


@pytest.fixture(scope="function")
def llm_actions_repo():
    repo = Repository()
    repo.init(include_base=True, include_templates=True)
    yield repo


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(
            path,
            id=path.stem,
        )
        for path in [LLM_TEMPLATES_DIR / "extract_one/openai.yml"]
    ],
)
def extract_one(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(
            path,
            id=path.stem,
        )
        for path in [LLM_TEMPLATES_DIR / "extract_many/openai.yml"]
    ],
)
def extract_many(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(
            path,
            id=path.stem,
        )
        for path in [LLM_TEMPLATES_DIR / "summarize/openai.yml"]
    ],
)
def summarize(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(
            path,
            id=path.stem,
        )
        for path in [LLM_TEMPLATES_DIR / "title/openai.yml"]
    ],
)
def title(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(
            path,
            id=path.stem,
        )
        for path in [LLM_TEMPLATES_DIR / "extract_data/openai.yml"]
    ],
)
def extract_data(request: pytest.FixtureRequest) -> TemplateAction:
    return load_yaml_template_action(request.param)


@pytest.mark.anyio
async def test_extract_one(
    extract_one: TemplateAction, llm_actions_repo: Repository
) -> None:
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
async def test_extract_many(
    extract_many: TemplateAction, llm_actions_repo: Repository
) -> None:
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
            "output_context": "Successful Y Combinator startups from San Francisco (e.g. 'Airbnb', 'Coinbase', 'Instacart)",
        },
        context={},
    )

    assert result is not None
    assert isinstance(result, dict)
    assert "companies" in result
    assert isinstance(result["companies"], list)

    companies = [company.lower() for company in result["companies"]]
    expected_companies = ["airbnb", "stripe", "dropbox", "doordash"]
    assert any(company in companies for company in expected_companies), (
        f"Expected at least one of the companies in the list: {expected_companies}. Got: {companies} instead."
    )


@pytest.mark.anyio
async def test_summarize(
    summarize: TemplateAction, llm_actions_repo: Repository
) -> None:
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

    max_length = 400
    result = await service.run_template_action(
        action=bound_action,
        args={
            "input": input_text,
            "input_context": "tech startup ecosystems",
            "max_length": max_length,
        },
        context={},
    )

    assert result is not None
    assert isinstance(result, str)
    assert len(result) <= max_length, f"Should be no more than {max_length} characters"
    assert any(
        term in result.lower() for term in ["y combinator", "startup", "tourism"]
    ), f"Should contain at least one of the terms: {result}"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "input,input_context,max_length",
    [
        (
            """
        Server logs show repeated connection attempts from IP 45.132.98.211 to our primary database server.
        Initial analysis suggests a potential brute force attack targeting SSH ports.
        The attacks started at 3:15 AM UTC and continued for approximately 45 minutes.
        Our IDS triggered 127 alerts during this time period.
        """,
            "security incident reports",
            80,
        ),
        (
            """
        The e-commerce checkout system is experiencing intermittent failures with order processing.
        Approximately 15% of transactions are failing with error code E-5501. Database metrics
        show increased latency during peak hours. The issue started after yesterday's deployment
        of the payment gateway microservice update (v2.4.1).
        """,
            "IT incident reports",
            70,
        ),
        (
            """
        During our scaling test of the recommendation engine, we observed memory leaks in the
        worker nodes. After 2 hours of continuous operation at 4x normal load,
        memory usage increased by 350% and didn't recover after garbage collection cycles.
        Performance degraded significantly after the 90-minute mark.
        """,
            "engineering problem reports",
            75,
        ),
        (
            """
        The automated CI/CD pipeline for the mobile app backend is failing at the integration
        test stage. The tests pass locally but fail in the Jenkins build environment.
        Error logs indicate timeout issues when connecting to the mock database service.
        This is blocking deployments for three feature teams.
        """,
            "DevOps alerts",
            65,
        ),
        (
            """
        Our primary Kubernetes cluster is experiencing a 23% increase in p95 latency across all services.
        Investigation shows node CPU throttling during peak traffic periods. Monitoring indicates that
        resource limits may be misconfigured on several critical microservices. Auto-scaling is not
        triggering as expected.
        """,
            "SRE incidents",
            85,
        ),
    ],
    ids=["security", "it", "engineering", "devops", "sre"],
)
async def test_title(
    title: TemplateAction,
    llm_actions_repo: Repository,
    input: str,
    input_context: str,
    max_length: int,
):
    """Test title generation with various scenarios, evaluated by an LLM judge."""
    llm_actions_repo.register_template_action(title)
    bound_action = llm_actions_repo.get(title.definition.action)

    # Run the title generation
    result = await service.run_template_action(
        action=bound_action,
        args={
            "input": input,
            "input_context": input_context,
            "max_length": max_length,
        },
        context={},
    )

    assert result is not None
    assert isinstance(result, str)
    assert len(result) <= max_length, f"Title exceeds max length of {max_length}"

    # Skip LLM judge if no OpenAI API key available
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OpenAI API key not available for LLM judge evaluation")

    # Extract domain expertise from input context
    domain = input_context.split()[
        0
    ].lower()  # Get first word (security, IT, engineering, etc.)

    # Use OpenAI's LLM as a judge to verify the title quality
    judge_prompt = f"""
    You are an expert {domain} professional who evaluates titles for {input_context}.

    <content>
    {input}
    </content>

    <generated_title>
    {result}
    </generated_title>

    <expert_criteria>
    As a {domain} expert, evaluate if this title:
    1. Effectively captures the primary issue
    2. Uses domain-appropriate terminology
    3. Provides sufficient context for a {domain} professional
    4. Is concise and clear (within {max_length} characters)
    5. Would be considered high-quality in professional {domain} communications
    </expert_criteria>

    For each criterion, provide a strict boolean assessment (true/false) based on your expertise in {domain}.
    You must be critical and apply professional standards - only answer true if the title genuinely satisfies the criterion.
    """

    verification_kwargs = {
        "prompt": judge_prompt,
        "model": "gpt-4o",
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "text_format": {
            "type": "json_schema",
            "name": "expert_title_evaluation",
            "schema": {
                "type": "object",
                "properties": {
                    "captures_primary_issue": {
                        "type": "boolean",
                        "description": "Whether the title correctly identifies and communicates the main problem described",
                    },
                    "uses_appropriate_terminology": {
                        "type": "boolean",
                        "description": f"Whether the title uses correct and proper {domain} terminology",
                    },
                    "provides_sufficient_context": {
                        "type": "boolean",
                        "description": f"Whether the title gives enough context for a {domain} professional to understand the situation",
                    },
                    "is_concise_and_clear": {
                        "type": "boolean",
                        "description": f"Whether the title is concise, clear, and within the {max_length} character limit",
                    },
                    "meets_professional_quality": {
                        "type": "boolean",
                        "description": f"Whether the overall title would be considered high-quality in professional {domain} communications",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Brief explanation of the assessment, highlighting strengths and weaknesses",
                    },
                },
                "required": [
                    "captures_primary_issue",
                    "uses_appropriate_terminology",
                    "provides_sufficient_context",
                    "is_concise_and_clear",
                    "meets_professional_quality",
                    "rationale",
                ],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }

    judge_response = await async_openai_call(**verification_kwargs)

    if (
        hasattr(judge_response, "incomplete_details")
        and judge_response.incomplete_details
    ):
        pytest.fail(
            f"LLM judge was unable to verify the title: {judge_response.incomplete_details}"
        )

    # Extract evaluation from judge response
    evaluation = None
    if hasattr(judge_response, "output_text"):
        evaluation = orjson.loads(judge_response.output_text)

    # Assert title quality using boolean checks - requiring only the most critical criteria
    assert evaluation is not None, "Failed to get evaluation from judge"

    # Must capture the primary issue - this is the most essential criterion
    assert evaluation["captures_primary_issue"], (
        f"Title '{result}' fails to capture the primary issue"
    )

    # Count how many other criteria pass
    criteria_count = sum(
        [
            evaluation["uses_appropriate_terminology"],
            evaluation["provides_sufficient_context"],
            evaluation["is_concise_and_clear"],
            evaluation["meets_professional_quality"],
        ]
    )

    # Require at least 2 of the 4 remaining criteria to pass
    assert criteria_count >= 2, (
        f"Title '{result}' only satisfies {criteria_count}/4 additional quality criteria. "
        f"Rationale: {evaluation['rationale']}"
    )


@pytest.mark.anyio
async def test_extract_data(
    extract_data: TemplateAction, llm_actions_repo: Repository
) -> None:
    """Test data extraction with multiple data values in a single input."""

    llm_actions_repo.register_template_action(extract_data)
    bound_action = llm_actions_repo.get(extract_data.definition.action)

    # Input with multiple security incidents
    input_data = """
    --- Incident 1 ---
    Incident ID: SEC-2023-0142
    Timestamp: 2023-07-15T03:15:27Z
    Source IP: 45.132.98.211
    Target: db-prod-01.example.com
    Method: SSH Brute Force
    Status: Contained

    --- Incident 2 ---
    Incident ID: SEC-2023-0157
    Timestamp: 2023-07-16T14:22:05Z
    Source IP: 193.27.14.65
    Target: auth-service.example.com
    Method: Credential Stuffing
    Status: Investigating

    --- Incident 3 ---
    Incident ID: SEC-2023-0163
    Timestamp: 2023-07-17T08:45:12Z
    Source IP: 91.134.175.89
    Target: api-gateway.example.com
    Method: SQL Injection Attempt
    Status: Mitigated
    """

    # Define test parameters
    input_context = "security incidents log"
    output_name = "incidents"
    output_schema = {
        "incident_id": {"type": "string"},
        "timestamp": {"type": "string"},
        "source_ip": {"type": "string"},
        "target": {"type": "string"},
        "method": {"type": "string"},
        "status": {"type": "string"},
    }
    expected_field_names = [
        "incident_id",
        "timestamp",
        "source_ip",
        "target",
        "method",
        "status",
    ]

    # Run the data extraction
    result = await service.run_template_action(
        action=bound_action,
        args={
            "input": input_data,
            "input_context": input_context,
            "output_name": output_name,
            "output_schema": output_schema,
        },
        context={},
    )

    # Log the result for debugging
    logger.debug(f"Extracted data: {result}")

    # Verify correct number of items and structure
    assert isinstance(result, list)
    assert len(result) == 3

    # Check field names to ensure structured output is properly configured
    assert all(field in result[0] for field in expected_field_names)


@pytest.mark.skip(reason="Not yet implemented")
@pytest.mark.anyio
async def test_extract_data_to_markdown(
    extract_data: TemplateAction, llm_actions_repo: Repository
) -> None:
    """Test that markdown output_format works correctly in extract_data template."""

    llm_actions_repo.register_template_action(extract_data)
    bound_action = llm_actions_repo.get(extract_data.definition.action)

    # Multiple security vulnerabilities in a single input
    input_data = """
    --- CVE-2023-1234 ---
    Title: Remote Code Execution in Authentication Service
    CVSS: 9.8 (Critical)
    Affected Versions: v1.2.0 - v1.5.3
    Vector: Network
    Status: Patched in v1.5.4

    --- CVE-2023-5678 ---
    Title: SQL Injection in User Profile API
    CVSS: 8.2 (High)
    Affected Versions: v1.3.0 - v1.5.2
    Vector: API
    Status: Patched in v1.5.3

    --- CVE-2023-9012 ---
    Title: Cross-Site Scripting in Admin Dashboard
    CVSS: 6.5 (Medium)
    Affected Versions: v1.4.0 - v1.5.4
    Vector: Web
    Status: Under Investigation
    """

    # Run the data extraction with markdown output format
    result = await service.run_template_action(
        action=bound_action,
        args={
            "input": input_data,
            "input_context": "security vulnerabilities",
            "output_name": "vulnerabilities",
            "output_schema": {
                "cve_id": {"type": "string"},
                "title": {"type": "string"},
                "cvss": {"type": "string"},
                "affected_versions": {"type": "string"},
                "vector": {"type": "string"},
                "status": {"type": "string"},
            },
            "output_format": "markdown",
        },
        context={},
    )

    # Log the result for debugging
    logger.debug(f"Markdown result:\n{result}")

    # Verify basic markdown format
    assert isinstance(result, str)

    # Check for header content to verify field names are preserved in markdown
    header_terms = ["cve", "title", "cvss", "affected", "vector", "status"]
    header = result.strip().split("\n")[0].lower()
    for term in header_terms:
        assert term in header, f"Expected field '{term}' missing from markdown header"

    # Count the rows to ensure we have at least header + separator + data rows
    rows = result.strip().split("\n")
    assert len(rows) >= 5
