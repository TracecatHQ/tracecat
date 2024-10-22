import os
from pathlib import Path

import pytest
import yaml
from jury.artifacts import load_file
from jury.judge.enums import Judgement
from jury.judge.service import Judge


@pytest.fixture(autouse=True, scope="session")
def monkeysession(request):
    mpatch = pytest.MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(scope="session")
def setup_openai(monkeysession: pytest.MonkeyPatch):
    if not os.getenv("GITHUB_ACTIONS"):
        import dotenv

        dotenv.load_dotenv()
    monkeysession.setenv("OPENAI_API_KEY", os.environ["OPENAI_API_KEY"])


def load_action(*, action: str, type: str, str_path: str):
    # Load the artifact
    path = Path(str_path)
    if type == "udf":
        pass
    else:
        if path.suffix != ".yml":
            raise ValueError(f"Unsupported file type: {path.suffix}")
        with path.open("r") as f:
            return yaml.safe_load(f)


@pytest.mark.parametrize(
    "action,type,source_path",
    [
        (
            "integrations.jira.create_issue",
            "template",
            "registry/tracecat_registry/templates/jira/create_issue.yml",
        ),
        (
            "integrations.jira.update_issue",
            "template",
            "registry/tracecat_registry/templates/jira/update_issue.yml",
        ),
    ],
)
def test_against_schema(setup_openai, action: str, type: str, source_path: str):
    judge = Judge()
    integration = load_action(action=action, type=type, str_path=source_path)
    schema = load_file(action)
    judgement = judge.verify(integration=integration, schema=schema)
    assert judgement == Judgement.YES
