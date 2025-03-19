"""Test LLM Action Templates."""

import json
import os
import uuid
from pathlib import Path

import pytest
import yaml

from tests.shared import glob_file_paths, load_yaml_template_action
from tracecat.concurrency import GatheringTaskGroup
from tracecat.executor import service
from tracecat.logger import logger
from tracecat.parse import to_flat_jsonpaths
from tracecat.registry.repository import Repository

TEST_TEMPLATES_DIR = Path("registry/tracecat_registry/templates/llm/guardduty")


@pytest.fixture(scope="function")
def repo(monkeysession):
    monkeysession.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))

    _repo = Repository()
    _repo.init(include_base=True, include_templates=True)
    for path in glob_file_paths(TEST_TEMPLATES_DIR, "yml"):
        _repo.register_template_action(load_yaml_template_action(path))
    yield _repo


# Fixture that loads .dev/gd.json
@pytest.fixture(scope="function")
def gd_json() -> dict:
    with Path("/Users/daryllim/dev/org/tracecat/.dev/gd.json").open("r") as f:
        return json.load(f)


# Dirty check to ensure that the all leaf values are found in the alert
def check_alert_values(alert: dict, result: dict) -> tuple[int, int]:
    flat_result = to_flat_jsonpaths(result)
    alert_yaml = yaml.dump(alert)
    total = 0
    count = 0
    for value in flat_result.values():
        total += 1
        if value:
            count += int(str(value) in alert_yaml)
    # Artbitrary heuristic to ensure that at least some values are found
    return count, total


@pytest.mark.anyio
async def test_alert_parser(repo: Repository, gd_json: dict) -> None:
    """Test that markdown output_format works correctly in extract_data template."""

    # A single GuardDuty finding
    input_data = gd_json

    # Run the data extraction with markdown output format
    alert_parser = repo.get("openai.guardduty.alert_parser")
    result = await service.run_template_action(
        action=alert_parser,
        args={
            "alert": input_data,
        },
        context={},
    )

    # Log the result for debugging
    logger.warning("Result", result=result)

    # Validate result structure
    assert "chain_of_thought" in result
    assert "who" in result
    assert "what" in result
    assert "when" in result
    assert "where" in result
    assert "why" in result
    assert "how" in result

    # Validate specific fields
    assert "actor" in result["who"]
    assert "finding_type" in result["what"]
    assert "first_seen" in result["when"]
    assert "region" in result["where"]
    assert "description" in result["why"]
    assert "technique" in result["how"]


@pytest.mark.anyio
async def test_extract_iocs(repo: Repository, gd_json: dict) -> None:
    """Test that markdown output_format works correctly in extract_data template."""

    # A single GuardDuty finding
    input_data = gd_json

    # Run the data extraction with markdown output format
    ioc_extractor = repo.get("openai.guardduty.ioc_extractor")
    result = await service.run_template_action(
        action=ioc_extractor,
        args={
            "alert": input_data,
            "input_context": "This is a recent GuardDuty finding",
            "output_name": "indicators",
        },
        context={},
    )

    # Log the result for debugging
    logger.warning("Result", result=result)

    # LLM judge
    assert False, json.dumps(result, indent=2)
    # count, total = check_alert_values(input_data, result)
    # assert count > 1, f"Only {count} out of {total} values found in alert"


RESULTS_PATH = Path(__file__).parent / ".results"


@pytest.mark.anyio
async def test_remediation_generator(repo: Repository, gd_json: dict) -> None:
    """Test that markdown output_format works correctly in extract_data template."""

    # A single GuardDuty finding
    session_id = str(uuid.uuid4())
    input_data = gd_json

    # Run the data extraction with markdown output format
    ioc_extractor = repo.get("openai.guardduty.ioc_extractor")
    alert_parser = repo.get("openai.guardduty.alert_parser")

    # Use VCR for recording the API calls
    async with GatheringTaskGroup() as tg:
        alert_parser_result = tg.create_task(
            service.run_template_action(
                action=alert_parser, args={"alert": input_data}, context={}
            )
        )
        ioc_extractor_result = tg.create_task(
            service.run_template_action(
                action=ioc_extractor,
                args={
                    "alert": input_data,
                    "input_context": "This is a recent GuardDuty finding",
                    "output_name": "indicators",
                },
                context={},
            )
        )
    alert_parser_result, ioc_extractor_result = tg.results()

    # Write to file with current timestamp
    with open(
        RESULTS_PATH / f"alert_parser_result_{session_id}.yml",
        "w",
    ) as f:
        yaml.safe_dump(alert_parser_result, f, indent=2)
    with open(
        RESULTS_PATH / f"ioc_extractor_result_{session_id}.yml",
        "w",
    ) as f:
        yaml.safe_dump(ioc_extractor_result, f, indent=2)

    # Run the remediation generator
    remediation_generator = repo.get("openai.guardduty.remediation_generator")
    # Run the remediation generator
    result = await service.run_template_action(
        action=remediation_generator,
        args={
            "alert": input_data,
            "alert_context": alert_parser_result,
            "iocs": ioc_extractor_result,
        },
        context={},
    )

    # Write to file with current timestamp
    with open(
        RESULTS_PATH / f"remediation_generator_result_{session_id}.yml",
        "w",
    ) as f:
        yaml.safe_dump(result, f, indent=2)

    # LLM judge
    assert False, json.dumps(result, indent=2)
